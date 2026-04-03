from __future__ import annotations

import argparse
import json
import sqlite3
import time
from collections import Counter
from pathlib import Path
from statistics import mean, median

from linuxdoscanner.ai_config import AIProviderConfig
from linuxdoscanner.classifier import TopicClassifier
from linuxdoscanner.discourse import strip_html
from linuxdoscanner.models import TopicPayload
from linuxdoscanner.settings import Settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe one topic repeatedly against an OpenAI-compatible model.")
    parser.add_argument("--topic-id", type=int, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--database",
        type=Path,
        default=None,
        help="SQLite database path.",
    )
    return parser.parse_args()


def load_payload(database_path: Path, topic_id: int) -> TopicPayload:
    with sqlite3.connect(database_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT
                topic_id, slug, title, url, category_id, category_name, tags_json,
                created_at, last_posted_at,
                author_username, author_display_name, author_avatar_url,
                first_post_html, topic_image_url, image_urls_json, external_links_json,
                reply_count, like_count, view_count, word_count, access_level
            FROM topics
            WHERE topic_id = ?
            """,
            (topic_id,),
        ).fetchone()
    if row is None:
        raise RuntimeError(f"Topic {topic_id} not found in {database_path}.")

    return TopicPayload(
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


def serialize_result(payload: TopicPayload, analysis) -> dict:
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


def build_metrics(runs: list[dict]) -> dict:
    ok_runs = [run for run in runs if run["status"] == "ok"]
    all_elapsed = [run["elapsed_seconds"] for run in runs]
    ok_elapsed = [run["elapsed_seconds"] for run in ok_runs]

    metrics = {
        "success_runs": len(ok_runs),
        "success_rate": round(len(ok_runs) / len(runs), 4) if runs else 0.0,
        "avg_elapsed_all_seconds": round(mean(all_elapsed), 3) if all_elapsed else None,
        "median_elapsed_all_seconds": round(median(all_elapsed), 3) if all_elapsed else None,
        "min_elapsed_all_seconds": round(min(all_elapsed), 3) if all_elapsed else None,
        "max_elapsed_all_seconds": round(max(all_elapsed), 3) if all_elapsed else None,
        "avg_elapsed_success_seconds": round(mean(ok_elapsed), 3) if ok_elapsed else None,
        "median_elapsed_success_seconds": round(median(ok_elapsed), 3) if ok_elapsed else None,
        "min_elapsed_success_seconds": round(min(ok_elapsed), 3) if ok_elapsed else None,
        "max_elapsed_success_seconds": round(max(ok_elapsed), 3) if ok_elapsed else None,
    }
    if not ok_runs:
        metrics["label_variants"] = {}
        metrics["notify_variants"] = {}
        metrics["exact_variants"] = {}
        return metrics

    label_counter = Counter(run["result"]["primary_label"] for run in ok_runs)
    notify_counter = Counter(run["result"]["requires_notification"] for run in ok_runs)
    exact_counter = Counter(
        (
            run["result"]["primary_label"],
            tuple(run["result"]["labels"]),
            run["result"]["requires_notification"],
            run["result"]["summary"],
        )
        for run in ok_runs
    )
    metrics["label_variants"] = dict(label_counter)
    metrics["notify_variants"] = {str(key).lower(): value for key, value in notify_counter.items()}
    metrics["exact_variants"] = {str(key): value for key, value in exact_counter.items()}
    return metrics


def main() -> int:
    args = parse_args()
    settings = Settings.from_env()
    settings.ensure_directories()
    settings.llm_batch_size = 1
    database_path = (args.database or settings.database_path).resolve()
    payload = load_payload(database_path, args.topic_id)

    ai_config = AIProviderConfig(
        provider_type="openai_compatible",
        base_url=args.base_url,
        api_key=args.api_key,
        selected_model=args.model,
    )
    classifier = TopicClassifier(settings, ai_config)
    if classifier._llm_http is None:
        raise RuntimeError("Classifier HTTP client failed to initialize.")

    runs: list[dict] = []
    try:
        for run_idx in range(1, args.runs + 1):
            started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            started = time.perf_counter()
            try:
                analysis = classifier._llm_analyze_batch_adaptive([payload])[0]
                elapsed = round(time.perf_counter() - started, 3)
                item = {
                    "run_idx": run_idx,
                    "started_at_utc": started_at,
                    "elapsed_seconds": elapsed,
                    "status": "ok",
                    "result": serialize_result(payload, analysis),
                }
            except Exception as exc:
                elapsed = round(time.perf_counter() - started, 3)
                item = {
                    "run_idx": run_idx,
                    "started_at_utc": started_at,
                    "elapsed_seconds": elapsed,
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                }
            runs.append(item)
            print(json.dumps(item, ensure_ascii=False))
    finally:
        classifier._llm_http.close()

    report = {
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "base_url": args.base_url,
        "model": args.model,
        "runs": args.runs,
        "topic": {
            "topic_id": payload.topic_id,
            "title": payload.title,
            "category_name": payload.category_name,
            "access_level": payload.access_level,
            "content_text_excerpt": (payload.content_text or "")[:500],
        },
        "metrics": build_metrics(runs),
        "run_results": runs,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote report to {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
