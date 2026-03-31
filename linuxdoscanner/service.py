from __future__ import annotations

from collections.abc import Callable
import logging
import time
from typing import Any

from .ai_config import AIConfigManager
from .classifier import TopicClassifier
from .discourse import APIAccessError, BrowserSessionManager, DiscourseAPIClient, build_topic_payload
from .models import TopicPayload
from .notify import NotificationDispatcher
from .notification_config import NotificationConfigManager
from .settings import Settings
from .storage import Database


LOGGER = logging.getLogger(__name__)

ProgressCallback = Callable[[dict[str, Any]], None]


class LinuxDoMonitor:
    def __init__(self, settings: Settings, enable_client: bool = True):
        self.settings = settings
        self.database = Database(settings.database_path)
        self.database.initialize()
        self.ai_config_manager = AIConfigManager(settings, self.database)
        self.notification_config_manager = NotificationConfigManager(settings, self.database)
        self.classifier = TopicClassifier(settings, ai_config=self.ai_config_manager.load_config())
        self.notifier = NotificationDispatcher(
            settings,
            notification_config=self.notification_config_manager.load_config(),
        )
        self.session_manager = BrowserSessionManager(settings) if enable_client else None
        self.client = DiscourseAPIClient(settings, self.session_manager) if enable_client else None

    def close(self) -> None:
        if self.client is not None:
            self.client.close()

    def probe(self) -> dict[str, str]:
        if self.client is None:
            raise RuntimeError("当前实例未启用抓取客户端，无法执行 probe。")
        return self.client.probe()

    def refresh_classifier(self) -> None:
        self.classifier = TopicClassifier(self.settings, ai_config=self.ai_config_manager.load_config())

    def refresh_notifier(self) -> None:
        self.notifier = NotificationDispatcher(
            self.settings,
            notification_config=self.notification_config_manager.load_config(),
        )

    def run_forever(self, interval_seconds: int | None = None) -> None:
        interval_seconds = interval_seconds or self.settings.poll_interval_seconds
        while True:
            self.run_once()
            LOGGER.info("Sleeping for %s seconds before next poll.", interval_seconds)
            time.sleep(interval_seconds)

    def run_once(self, bootstrap_limit: int | None = None) -> list[TopicPayload]:
        if self.client is None:
            raise RuntimeError("当前实例未启用抓取客户端，无法执行 run-once。")
        self.refresh_classifier()
        self.refresh_notifier()
        category_map = self.client.load_categories()
        last_seen_topic_id = self.database.get_last_seen_topic_id()
        bootstrap_limit = bootstrap_limit or self.settings.bootstrap_limit
        summaries = self._collect_new_topic_summaries(last_seen_topic_id, bootstrap_limit)

        if not summaries:
            LOGGER.info("No new topics found.")
            return []

        payloads: list[TopicPayload] = []
        for summary in summaries:
            topic_id = int(summary["id"])
            detail = None
            try:
                detail = self.client.fetch_topic_detail(topic_id=topic_id, slug=summary["slug"])
            except APIAccessError as exc:
                LOGGER.warning("Topic %s detail fetch failed, saving summary only: %s", topic_id, exc)
            payload = build_topic_payload(
                base_url=self.settings.base_url,
                summary=summary,
                detail=detail,
                category_map=category_map,
            )
            payloads.append(payload)
        self._store_payloads(payloads, previous_last_seen_topic_id=last_seen_topic_id)
        return payloads

    def ingest_topic_documents(
        self,
        topic_documents: list[dict[str, Any]],
        category_map: dict[int, str] | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> list[TopicPayload]:
        self.refresh_classifier()
        self.refresh_notifier()
        category_map = category_map or {}
        last_seen_topic_id = self.database.get_last_seen_topic_id()
        self._emit_progress(
            progress_callback,
            percent=89,
            stage="bridge-ingest",
            label="整理服务端任务",
            detail=f"收到 {len(topic_documents)} 个主题，正在筛选新增内容",
        )
        payloads: list[TopicPayload] = []
        for document in topic_documents:
            summary = document.get("summary") or {}
            if "id" not in summary or "slug" not in summary or "title" not in summary:
                LOGGER.warning("Skipping malformed topic document without required summary fields: %s", summary)
                continue
            topic_id = int(summary["id"])
            if last_seen_topic_id is not None and topic_id <= last_seen_topic_id:
                continue
            detail = document.get("detail")
            payloads.append(
                build_topic_payload(
                    base_url=self.settings.base_url,
                    summary=summary,
                    detail=detail if isinstance(detail, dict) else None,
                    category_map=category_map,
                )
            )

        if payloads:
            self._emit_progress(
                progress_callback,
                percent=90,
                stage="bridge-ingest",
                label="整理新增主题",
                detail=f"已筛出 {len(payloads)} 个新主题，准备进入 AI 识别",
            )
        else:
            self._emit_progress(
                progress_callback,
                percent=99,
                stage="bridge-ingest",
                label="没有发现新主题",
                detail="当前批次没有需要入库的新主题",
            )

        self._store_payloads(
            payloads,
            previous_last_seen_topic_id=last_seen_topic_id,
            progress_callback=progress_callback,
        )
        return payloads

    def _collect_new_topic_summaries(
        self,
        last_seen_topic_id: int | None,
        bootstrap_limit: int,
    ) -> list[dict[str, Any]]:
        new_topics: list[dict[str, Any]] = []
        seen_ids: set[int] = set()

        for page_number in range(self.settings.max_pages_per_run):
            data = self.client.fetch_latest_page(page_number)
            topics = data.get("topic_list", {}).get("topics", [])
            if not topics:
                break

            for topic in topics:
                topic_id = int(topic["id"])
                if topic_id in seen_ids:
                    continue
                seen_ids.add(topic_id)

                if last_seen_topic_id is None:
                    new_topics.append(topic)
                    if len(new_topics) >= bootstrap_limit:
                        return new_topics
                    continue

                if topic_id <= last_seen_topic_id:
                    return new_topics

                new_topics.append(topic)

        return new_topics

    def _store_payloads(
        self,
        payloads: list[TopicPayload],
        previous_last_seen_topic_id: int | None,
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        if not payloads:
            LOGGER.info("No new topics found.")
            self._emit_progress(
                progress_callback,
                percent=100,
                stage="completed",
                label="同步完成",
                detail="本轮没有新增主题",
            )
            return

        self.refresh_classifier()
        self.refresh_notifier()
        max_topic_id = previous_last_seen_topic_id or 0
        pending_retry_topic_ids = set(self.database.list_pending_ai_retry_topic_ids())
        total_payloads = len(payloads)
        llm_batch_size = max(1, int(getattr(self.settings, "llm_batch_size", 1) or 1))
        expected_batch_count = max(1, (total_payloads + llm_batch_size - 1) // llm_batch_size)
        self._emit_progress(
            progress_callback,
            percent=91,
            stage="ai-classify",
            label="等待 AI 响应",
            detail=f"共 {total_payloads} 个主题，预计分 {expected_batch_count} 批完成 AI 识别",
        )

        def on_classifier_progress(event: dict[str, Any]) -> None:
            event_name = str(event.get("event") or "")
            completed_topics = int(event.get("completed_topics") or 0)
            total_topics = max(1, int(event.get("total_topics") or total_payloads))
            if event_name == "unavailable":
                self._emit_progress(
                    progress_callback,
                    percent=94,
                    stage="ai-classify",
                    label="AI 当前不可用",
                    detail="当前未配置可用 AI，正在按空标签结果继续入库",
                )
                return

            if event_name == "retry_split":
                self._emit_progress(
                    progress_callback,
                    percent=min(95, 91 + round(4 * completed_topics / total_topics)),
                    stage="ai-classify",
                    label="AI 响应过慢，正在拆分重试",
                    detail=(
                        f"当前批次 {int(event.get('batch_topic_count') or 0)} 个主题过大，"
                        f"正在拆成 {int(event.get('left_size') or 0)} + {int(event.get('right_size') or 0)} 重试"
                    ),
                )
                return

            batch_index = int(event.get("batch_index") or 1)
            batch_count = max(1, int(event.get("batch_count") or expected_batch_count))
            batch_topic_count = int(event.get("batch_topic_count") or 0)
            if event_name == "batch_start":
                self._emit_progress(
                    progress_callback,
                    percent=min(95, 91 + round(4 * completed_topics / total_topics)),
                    stage="ai-classify",
                    label="等待 AI 响应",
                    detail=(
                        f"正在等待 AI 返回第 {batch_index}/{batch_count} 批结果，"
                        f"本批 {batch_topic_count} 个主题，已完成 {completed_topics}/{total_payloads}"
                    ),
                )
                return

            if event_name == "batch_complete":
                self._emit_progress(
                    progress_callback,
                    percent=min(95, 91 + round(4 * completed_topics / total_topics)),
                    stage="ai-classify",
                    label="AI 识别进行中",
                    detail=f"AI 已完成 {completed_topics}/{total_payloads} 个主题（第 {batch_index}/{batch_count} 批已返回）",
                )

        results = self.classifier.analyze_many_detailed(payloads, progress_callback=on_classifier_progress)
        self._emit_progress(
            progress_callback,
            percent=95,
            stage="database-write",
            label="写入数据库",
            detail=f"正在写入 {total_payloads} 个主题的识别结果",
        )
        saw_successful_ai_request = False
        for index, (payload, result) in enumerate(zip(payloads, results, strict=False), start=1):
            self.database.upsert_topic(payload, result.analysis)
            max_topic_id = max(max_topic_id, payload.topic_id)
            if result.request_succeeded:
                saw_successful_ai_request = True
                self.database.mark_ai_retry_succeeded(payload.topic_id)
            elif result.should_retry:
                self.database.enqueue_ai_retry(
                    payload,
                    failure_reason=result.failure_reason,
                    max_retries=self.settings.llm_retry_limit,
                )
            LOGGER.info(
                "Stored topic %s | %s | %s",
                payload.topic_id,
                payload.category_name or "未分类",
                payload.title,
            )
            if index == total_payloads or index == 1 or index % 10 == 0:
                self._emit_progress(
                    progress_callback,
                    percent=min(97, 95 + round(2 * index / total_payloads)),
                    stage="database-write",
                    label="写入数据库",
                    detail=f"已写入 {index}/{total_payloads} 个主题",
                )

        if max_topic_id:
            self.database.set_last_seen_topic_id(max_topic_id)

        if saw_successful_ai_request and pending_retry_topic_ids:
            self._emit_progress(
                progress_callback,
                percent=97,
                stage="ai-retry",
                label="补偿重试历史 AI 失败",
                detail=f"正在重试 {len(pending_retry_topic_ids)} 个历史失败主题",
            )
            self._retry_previously_failed_payloads(
                pending_retry_topic_ids,
                progress_callback=progress_callback,
            )

        pending = self.database.get_pending_notifications()
        if pending and self.notifier.is_configured():
            self._emit_progress(
                progress_callback,
                percent=99,
                stage="notify",
                label="发送通知",
                detail=f"检测到 {len(pending)} 条需要通知的主题，正在发送",
            )
            try:
                topic_ids = self.notifier.send(pending)
            except Exception as exc:
                LOGGER.warning("Notification delivery failed: %s", exc)
            else:
                self.database.mark_topics_notified(topic_ids)
                LOGGER.info("Sent notification for %s topics.", len(topic_ids))
        elif pending:
            LOGGER.info(
                "Found %s topics worth notifying, but no notification channel is configured.",
                len(pending),
            )
            self._emit_progress(
                progress_callback,
                percent=99,
                stage="notify",
                label="跳过通知发送",
                detail=f"有 {len(pending)} 条主题值得通知，但当前没有可用通知通道",
            )
        else:
            self._emit_progress(
                progress_callback,
                percent=99,
                stage="notify",
                label="无需发送通知",
                detail="本轮没有命中通知条件的主题",
            )

        self._emit_progress(
            progress_callback,
            percent=100,
            stage="completed",
            label="同步完成",
            detail=f"本轮新增入库 {len(payloads)} 个主题",
        )

    def _retry_previously_failed_payloads(
        self,
        topic_ids: set[int],
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        retries = self.database.get_pending_ai_retries(topic_ids)
        if not retries:
            return

        payloads = [retry.payload for retry in retries]
        retry_lookup = {retry.topic_id: retry for retry in retries}
        LOGGER.info(
            "Retrying %s previously failed AI requests after a successful AI call.",
            len(payloads),
        )
        retry_total = len(payloads)

        def on_retry_progress(event: dict[str, Any]) -> None:
            event_name = str(event.get("event") or "")
            completed_topics = int(event.get("completed_topics") or 0)
            total_topics = max(1, int(event.get("total_topics") or retry_total))
            if event_name == "retry_split":
                self._emit_progress(
                    progress_callback,
                    percent=min(98, 97 + round(completed_topics / total_topics)),
                    stage="ai-retry",
                    label="补偿重试历史 AI 失败",
                    detail=(
                        f"历史失败批次过大，正在拆成 {int(event.get('left_size') or 0)} + "
                        f"{int(event.get('right_size') or 0)} 重试"
                    ),
                )
                return

            batch_index = int(event.get("batch_index") or 1)
            batch_count = max(1, int(event.get("batch_count") or 1))
            if event_name == "batch_start":
                self._emit_progress(
                    progress_callback,
                    percent=min(98, 97 + round(completed_topics / total_topics)),
                    stage="ai-retry",
                    label="补偿重试历史 AI 失败",
                    detail=f"正在重试第 {batch_index}/{batch_count} 批历史失败主题",
                )
                return

            if event_name == "batch_complete":
                self._emit_progress(
                    progress_callback,
                    percent=min(98, 97 + round(completed_topics / total_topics)),
                    stage="ai-retry",
                    label="补偿重试历史 AI 失败",
                    detail=f"历史失败主题已补偿 {completed_topics}/{retry_total}",
                )

        results = self.classifier.analyze_many_detailed(payloads, progress_callback=on_retry_progress)
        for payload, result in zip(payloads, results, strict=False):
            self.database.upsert_topic(payload, result.analysis)
            if result.request_succeeded:
                self.database.mark_ai_retry_succeeded(payload.topic_id)
                LOGGER.info("AI retry succeeded for topic %s.", payload.topic_id)
                continue

            if result.should_retry:
                updated = self.database.increment_ai_retry_failure(
                    payload,
                    failure_reason=result.failure_reason,
                )
                if updated is not None and updated.status == "exhausted":
                    LOGGER.warning(
                        "AI retry exhausted for topic %s after %s retries.",
                        payload.topic_id,
                        updated.retry_count,
                    )
                else:
                    retry_count = updated.retry_count if updated is not None else retry_lookup[payload.topic_id].retry_count
                    LOGGER.warning(
                        "AI retry failed for topic %s; will retry again later (%s/%s).",
                        payload.topic_id,
                        retry_count,
                        retry_lookup[payload.topic_id].max_retries,
                    )

    def _emit_progress(
        self,
        progress_callback: ProgressCallback | None,
        *,
        percent: int,
        stage: str,
        label: str,
        detail: str,
    ) -> None:
        if progress_callback is None:
            return
        progress_callback(
            {
                "percent": max(0, min(100, int(percent))),
                "stage": stage,
                "label": label,
                "detail": detail,
            }
        )
