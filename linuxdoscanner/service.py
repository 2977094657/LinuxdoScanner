from __future__ import annotations

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
    ) -> list[TopicPayload]:
        self.refresh_classifier()
        self.refresh_notifier()
        category_map = category_map or {}
        last_seen_topic_id = self.database.get_last_seen_topic_id()
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

        self._store_payloads(payloads, previous_last_seen_topic_id=last_seen_topic_id)
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
    ) -> None:
        if not payloads:
            LOGGER.info("No new topics found.")
            return

        self.refresh_classifier()
        self.refresh_notifier()
        max_topic_id = previous_last_seen_topic_id or 0
        analyses = self.classifier.analyze_many(payloads)
        for payload, analysis in zip(payloads, analyses, strict=False):
            self.database.upsert_topic(payload, analysis)
            max_topic_id = max(max_topic_id, payload.topic_id)
            LOGGER.info(
                "Stored topic %s | %s | %s",
                payload.topic_id,
                payload.category_name or "未分类",
                payload.title,
            )

        if max_topic_id:
            self.database.set_last_seen_topic_id(max_topic_id)

        pending = self.database.get_pending_notifications()
        if pending and self.notifier.is_configured():
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
