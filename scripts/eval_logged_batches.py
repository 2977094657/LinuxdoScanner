from __future__ import annotations

import argparse
import json
import sqlite3
import time
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median
from typing import Any

from linuxdoscanner.ai_config import AIProviderConfig
from linuxdoscanner.classifier import TopicClassifier
from linuxdoscanner.discourse import strip_html
from linuxdoscanner.models import TopicPayload
from linuxdoscanner.settings import DEFAULT_EVAL_REPORT_PATH, Settings


BATCH_SPECS = [
    {
        "batch_idx": 1,
        "logged_at": "2026-03-29 20:02:29",
        "topic_ids": [1846944, 1846938, 1846933, 1846928, 1846925],
        "note": "真实日志批次，包含 AI 平台地区封禁、福利卡片和普通水贴混合样本。",
    },
    {
        "batch_idx": 2,
        "logged_at": "2026-03-29 20:07:30",
        "topic_ids": [1846976, 1846966, 1846960, 1846957, 1846949],
        "note": "真实日志批次，包含生日树洞、AI 工具提问、注册成功庆祝等易误判样本。",
    },
    {
        "batch_idx": 3,
        "logged_at": "2026-03-29 20:12:29",
        "topic_ids": [1847006, 1847001, 1847000, 1846994, 1846993, 1846989],
        "note": "真实日志批次，包含公益站、ChatGPT workspace 退出技巧和多条普通求助。",
    },
]


MANUAL_NOTIFY_EXPECTED: dict[int, dict[str, Any]] = {
    1846944: {"notify": False, "note": "单纯显摆 GPTfree 用得顺，没有新增方法论。"},
    1846938: {"notify": False, "note": "新闻是真实，但和当前 AI 关注点无关。"},
    1846933: {"notify": True, "note": "AI 平台地区封禁变化，时效性和行动价值都很强。"},
    1846928: {"notify": True, "note": "明确可领的 GLM 体验卡福利。"},
    1846925: {"notify": False, "note": "缺少正文证据，偏水贴。"},
    1846976: {"notify": False, "note": "生日树洞。"},
    1846966: {"notify": False, "note": "纯提问，没有可复用做法。"},
    1846960: {"notify": False, "note": "注册机故障求助，信息密度不足。"},
    1846957: {"notify": False, "note": "Vibe Coding 方案征集，仍属纯提问。"},
    1846949: {"notify": False, "note": "注册成功庆祝，不应通知。"},
    1847006: {"notify": False, "note": "求教程的纯提问。"},
    1847001: {"notify": False, "note": "注册成功庆祝。"},
    1847000: {"notify": False, "note": "泛讨论，不是当前关注点。"},
    1846994: {"notify": False, "note": "甲骨文机器求助，和 AI 关注点无关。"},
    1846993: {"notify": True, "note": "明确公益站入口和可用性信息。"},
    1846989: {"notify": True, "note": "ChatGPT workspace 退出技巧，具备直接操作价值。"},
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate logged Linux.do AI batches against multiple models.")
    parser.add_argument(
        "--models",
        nargs="+",
        default=["deepseek/deepseek-v3.2", "minimax/minimax-m2.5"],
        help="Models to evaluate via the currently configured OpenAI-compatible endpoint.",
    )
    parser.add_argument("--runs", type=int, default=3, help="Number of runs per batch per model.")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_EVAL_REPORT_PATH,
        help="Where to write the combined JSON report.",
    )
    parser.add_argument("--base-url", help="Override the OpenAI-compatible base URL for this run.")
    parser.add_argument("--api-key", help="Override the API key for this run.")
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=1.0,
        help="Pause between runs to reduce burstiness against the upstream endpoint.",
    )
    return parser.parse_args()


def load_ai_config(database_path: Path) -> AIProviderConfig:
    with sqlite3.connect(database_path) as conn:
        row = conn.execute("SELECT value FROM app_config WHERE key = 'ai_config'").fetchone()
    if row is None or not row[0]:
        raise RuntimeError(f"Database {database_path} does not contain app_config.ai_config.")
    return AIProviderConfig.from_dict(json.loads(row[0]))


def override_ai_config(
    *,
    base_config: AIProviderConfig,
    base_url: str | None,
    api_key: str | None,
) -> AIProviderConfig:
    payload = base_config.to_dict()
    if base_url:
        payload["base_url"] = base_url.strip()
    if api_key:
        payload["api_key"] = api_key.strip()
    return AIProviderConfig.from_dict(payload)


def load_topic_payloads(database_path: Path) -> dict[int, TopicPayload]:
    topic_ids = [topic_id for batch in BATCH_SPECS for topic_id in batch["topic_ids"]]
    placeholders = ", ".join("?" for _ in topic_ids)
    query = f"""
        SELECT
            topic_id, slug, title, url, category_id, category_name, tags_json,
            created_at, last_posted_at,
            author_username, author_display_name, author_avatar_url,
            first_post_html, topic_image_url, image_urls_json, external_links_json,
            reply_count, like_count, view_count, word_count, access_level,
            ai_provider, ai_label, ai_summary, ai_reasons_json, ai_labels_json, requires_notification
        FROM topics
        WHERE topic_id IN ({placeholders})
    """
    with sqlite3.connect(database_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, topic_ids).fetchall()

    payloads: dict[int, TopicPayload] = {}
    for row in rows:
        payloads[int(row["topic_id"])] = TopicPayload(
            topic_id=int(row["topic_id"]),
            slug=row["slug"],
            title=row["title"],
            url=row["url"],
            category_id=row["category_id"],
            category_name=row["category_name"],
            tags=json.loads(row["tags_json"] or "[]"),
            created_at=row["created_at"],
            last_posted_at=row["last_posted_at"],
            author_username=row["author_username"],
            author_display_name=row["author_display_name"],
            author_avatar_url=row["author_avatar_url"],
            first_post_html=row["first_post_html"],
            content_text=strip_html(row["first_post_html"]) or row["title"],
            topic_image_url=row["topic_image_url"],
            image_urls=json.loads(row["image_urls_json"] or "[]"),
            external_links=json.loads(row["external_links_json"] or "[]"),
            reply_count=row["reply_count"],
            like_count=row["like_count"],
            view_count=row["view_count"],
            word_count=row["word_count"],
            access_level=row["access_level"] or "public",
        )
    missing = [topic_id for topic_id in topic_ids if topic_id not in payloads]
    if missing:
        raise RuntimeError(f"Missing topic payloads in database: {missing}")
    return payloads


def load_baseline(database_path: Path) -> dict[int, dict[str, Any]]:
    topic_ids = [topic_id for batch in BATCH_SPECS for topic_id in batch["topic_ids"]]
    placeholders = ", ".join("?" for _ in topic_ids)
    query = f"""
        SELECT topic_id, ai_provider, ai_label, ai_summary, ai_labels_json, requires_notification
        FROM topics
        WHERE topic_id IN ({placeholders})
    """
    with sqlite3.connect(database_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, topic_ids).fetchall()
    baseline: dict[int, dict[str, Any]] = {}
    for row in rows:
        baseline[int(row["topic_id"])] = {
            "provider": row["ai_provider"],
            "primary_label": row["ai_label"],
            "labels": json.loads(row["ai_labels_json"] or "[]"),
            "summary": row["ai_summary"],
            "requires_notification": bool(row["requires_notification"]),
        }
    return baseline


def serialize_analysis(payload: TopicPayload, analysis: Any) -> dict[str, Any]:
    return {
        "topic_id": payload.topic_id,
        "title": payload.title,
        "primary_label": analysis.primary_label,
        "labels": list(analysis.labels),
        "summary": analysis.summary,
        "reasons": list(analysis.reasons),
        "requires_notification": bool(analysis.requires_notification),
        "provider": analysis.provider,
    }


def build_settings() -> Settings:
    settings = Settings.from_env()
    settings.ensure_directories()
    settings.llm_batch_size = 10
    return settings


def evaluate_model(
    *,
    model: str,
    base_config: AIProviderConfig,
    payload_lookup: dict[int, TopicPayload],
    runs: int,
    sleep_seconds: float,
) -> dict[str, Any]:
    model_config = AIProviderConfig.from_dict({**base_config.to_dict(), "selected_model": model})
    settings = build_settings()
    classifier = TopicClassifier(settings, model_config)
    if classifier._llm_http is None:
        raise RuntimeError(f"Classifier client could not be initialized for model {model}.")

    all_runs: list[dict[str, Any]] = []
    try:
        for batch in BATCH_SPECS:
            payloads = [payload_lookup[topic_id] for topic_id in batch["topic_ids"]]
            for run_idx in range(1, runs + 1):
                started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                started_perf = time.perf_counter()
                try:
                    analyses = classifier._llm_analyze_batch_adaptive(payloads)
                    elapsed = round(time.perf_counter() - started_perf, 3)
                    result = {
                        "model": model,
                        "batch_idx": batch["batch_idx"],
                        "logged_at": batch["logged_at"],
                        "run_idx": run_idx,
                        "started_at_utc": started_at,
                        "elapsed_seconds": elapsed,
                        "status": "ok",
                        "topic_ids": list(batch["topic_ids"]),
                        "results": [
                            serialize_analysis(payload, analysis)
                            for payload, analysis in zip(payloads, analyses, strict=True)
                        ],
                    }
                except Exception as exc:
                    elapsed = round(time.perf_counter() - started_perf, 3)
                    result = {
                        "model": model,
                        "batch_idx": batch["batch_idx"],
                        "logged_at": batch["logged_at"],
                        "run_idx": run_idx,
                        "started_at_utc": started_at,
                        "elapsed_seconds": elapsed,
                        "status": "error",
                        "topic_ids": list(batch["topic_ids"]),
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                    }
                all_runs.append(result)
                if sleep_seconds > 0:
                    time.sleep(sleep_seconds)
    finally:
        classifier._llm_http.close()

    return {
        "model": model,
        "runs": all_runs,
        "metrics": build_metrics(model=model, runs=all_runs),
    }


def build_metrics(*, model: str, runs: list[dict[str, Any]]) -> dict[str, Any]:
    ok_runs = [run for run in runs if run["status"] == "ok"]
    elapsed_values = [run["elapsed_seconds"] for run in ok_runs]

    per_batch: list[dict[str, Any]] = []
    for batch in BATCH_SPECS:
        batch_runs = [run for run in runs if run["batch_idx"] == batch["batch_idx"]]
        batch_ok = [run for run in batch_runs if run["status"] == "ok"]
        per_batch.append(
            {
                "batch_idx": batch["batch_idx"],
                "logged_at": batch["logged_at"],
                "topic_ids": list(batch["topic_ids"]),
                "success_runs": len(batch_ok),
                "avg_elapsed_seconds": round(mean(run["elapsed_seconds"] for run in batch_ok), 3) if batch_ok else None,
                "min_elapsed_seconds": round(min((run["elapsed_seconds"] for run in batch_ok)), 3) if batch_ok else None,
                "max_elapsed_seconds": round(max((run["elapsed_seconds"] for run in batch_ok)), 3) if batch_ok else None,
            }
        )

    topic_records: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for run in ok_runs:
        for item in run["results"]:
            topic_records[int(item["topic_id"])].append(item)

    topic_consistency: list[dict[str, Any]] = []
    notify_correct = 0
    notify_total = 0
    majority_notify_correct = 0
    majority_total = 0
    false_positive_topics: list[int] = []
    false_negative_topics: list[int] = []
    for topic_id, records in sorted(topic_records.items()):
        label_counter = Counter(record["primary_label"] for record in records)
        notify_counter = Counter(bool(record["requires_notification"]) for record in records)
        exact_counter = Counter(
            (
                record["primary_label"],
                tuple(record["labels"]),
                bool(record["requires_notification"]),
            )
            for record in records
        )
        expected_notify = MANUAL_NOTIFY_EXPECTED.get(topic_id, {}).get("notify")
        for record in records:
            if expected_notify is None:
                continue
            notify_total += 1
            notify_correct += int(bool(record["requires_notification"]) == expected_notify)

        majority_notify = notify_counter.most_common(1)[0][0]
        if expected_notify is not None:
            majority_total += 1
            is_majority_correct = bool(majority_notify) == expected_notify
            majority_notify_correct += int(is_majority_correct)
            if not is_majority_correct and majority_notify:
                false_positive_topics.append(topic_id)
            if not is_majority_correct and not majority_notify:
                false_negative_topics.append(topic_id)

        topic_consistency.append(
            {
                "topic_id": topic_id,
                "runs_captured": len(records),
                "majority_label": label_counter.most_common(1)[0][0],
                "majority_label_share": round(label_counter.most_common(1)[0][1] / len(records), 4),
                "majority_notify": bool(majority_notify),
                "majority_notify_share": round(notify_counter.most_common(1)[0][1] / len(records), 4),
                "majority_exact_share": round(exact_counter.most_common(1)[0][1] / len(records), 4),
                "label_variants": dict(label_counter),
                "notify_variants": {str(key).lower(): value for key, value in notify_counter.items()},
                "manual_notify_expected": expected_notify,
                "manual_note": MANUAL_NOTIFY_EXPECTED.get(topic_id, {}).get("note"),
            }
        )

    return {
        "model": model,
        "total_runs": len(runs),
        "success_runs": len(ok_runs),
        "success_rate": round(len(ok_runs) / len(runs), 4) if runs else 0.0,
        "avg_elapsed_seconds": round(mean(elapsed_values), 3) if elapsed_values else None,
        "median_elapsed_seconds": round(median(elapsed_values), 3) if elapsed_values else None,
        "min_elapsed_seconds": round(min(elapsed_values), 3) if elapsed_values else None,
        "max_elapsed_seconds": round(max(elapsed_values), 3) if elapsed_values else None,
        "notify_accuracy_per_run": round(notify_correct / notify_total, 4) if notify_total else None,
        "notify_accuracy_majority": round(majority_notify_correct / majority_total, 4) if majority_total else None,
        "avg_majority_label_share": round(mean(item["majority_label_share"] for item in topic_consistency), 4)
        if topic_consistency
        else None,
        "avg_majority_notify_share": round(mean(item["majority_notify_share"] for item in topic_consistency), 4)
        if topic_consistency
        else None,
        "avg_majority_exact_share": round(mean(item["majority_exact_share"] for item in topic_consistency), 4)
        if topic_consistency
        else None,
        "false_positive_topics": false_positive_topics,
        "false_negative_topics": false_negative_topics,
        "per_batch": per_batch,
        "per_topic_consistency": topic_consistency,
    }


def build_topic_catalog(payload_lookup: dict[int, TopicPayload], baseline: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    catalog: list[dict[str, Any]] = []
    for batch in BATCH_SPECS:
        for topic_id in batch["topic_ids"]:
            payload = payload_lookup[topic_id]
            catalog.append(
                {
                    "batch_idx": batch["batch_idx"],
                    "topic_id": topic_id,
                    "title": payload.title,
                    "category_name": payload.category_name,
                    "access_level": payload.access_level,
                    "content_text_excerpt": (payload.content_text or "")[:280],
                    "baseline": baseline.get(topic_id),
                    "manual_notify_expected": MANUAL_NOTIFY_EXPECTED.get(topic_id, {}).get("notify"),
                    "manual_note": MANUAL_NOTIFY_EXPECTED.get(topic_id, {}).get("note"),
                }
            )
    return catalog


def main() -> int:
    args = parse_args()
    settings = build_settings()
    ai_config = override_ai_config(
        base_config=load_ai_config(settings.database_path),
        base_url=args.base_url,
        api_key=args.api_key,
    )
    payload_lookup = load_topic_payloads(settings.database_path)
    baseline = load_baseline(settings.database_path)

    report = {
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "endpoint_base_url": ai_config.base_url,
        "source_database": str(settings.database_path),
        "source_batches": BATCH_SPECS,
        "models": args.models,
        "runs_per_batch": args.runs,
        "baseline_model_in_db": ai_config.selected_model,
        "topic_catalog": build_topic_catalog(payload_lookup, baseline),
        "evaluations": [],
    }

    for model in args.models:
        report["evaluations"].append(
            evaluate_model(
                model=model,
                base_config=ai_config,
                payload_lookup=payload_lookup,
                runs=args.runs,
                sleep_seconds=args.sleep_seconds,
            )
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote report to {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
