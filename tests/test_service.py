from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from linuxdoscanner.models import TopicAnalysis, TopicAnalysisResult, TopicPayload
from linuxdoscanner.service import LinuxDoMonitor
from linuxdoscanner.storage import Database


def build_payload(topic_id: int, *, title: str) -> TopicPayload:
    return TopicPayload(
        topic_id=topic_id,
        slug=f"topic-{topic_id}",
        title=title,
        url=f"https://linux.do/t/topic/{topic_id}",
        category_id=11,
        category_name="搞七捻三",
        tags=["AI相关"],
        created_at="2026-03-30T00:00:00+00:00",
        last_posted_at="2026-03-30T00:05:00+00:00",
        author_username="tester",
        author_display_name="Tester",
        first_post_html=f"<p>{title}</p>",
        content_text=title,
        reply_count=1,
        like_count=2,
        view_count=3,
        word_count=10,
    )


def build_topic_document(topic_id: int, *, title: str) -> dict[str, object]:
    return {
        "summary": {
            "id": topic_id,
            "slug": f"topic-{topic_id}",
            "title": title,
            "category_id": 11,
            "tags": ["AI相关"],
            "created_at": "2026-03-30T00:00:00+00:00",
            "last_posted_at": "2026-03-30T00:05:00+00:00",
            "reply_count": 1,
            "like_count": 2,
            "views": 3,
            "excerpt": title,
        },
        "detail": {
            "post_stream": {
                "posts": [
                    {
                        "username": "tester",
                        "name": "Tester",
                        "avatar_template": "/user_avatar/linux.do/tester/{size}/1_2.png",
                        "cooked": f"<p>{title}</p>",
                        "word_count": 10,
                    }
                ]
            },
            "reply_count": 1,
            "like_count": 2,
            "views": 3,
            "word_count": 10,
        },
    }


def success_result(label: str) -> TopicAnalysisResult:
    return TopicAnalysisResult(
        analysis=TopicAnalysis(
            primary_label=label,
            labels=["AI相关", label],
            summary=f"命中 {label}",
            reasons=["包含有效信息"],
            provider="llm:test-model",
            requires_notification=True,
        ),
        request_succeeded=True,
    )


def failure_result(reason: str) -> TopicAnalysisResult:
    return TopicAnalysisResult(
        analysis=TopicAnalysis(
            primary_label="AI识别失败",
            labels=["AI识别失败"],
            summary="AI 识别失败，当前批次未生成标签。",
            reasons=["AI 接口请求失败，未使用规则兜底。"],
            provider="llm_failed",
            requires_notification=False,
        ),
        request_succeeded=False,
        should_retry=True,
        failure_reason=reason,
    )


class FakeClassifier:
    def __init__(self, plans: list[list[TopicAnalysisResult]]):
        self.plans = list(plans)
        self.calls: list[list[int]] = []
        self.progress_events: list[list[dict[str, object]]] = []

    def analyze_many_detailed(
        self,
        payloads: list[TopicPayload],
        progress_callback=None,
    ) -> list[TopicAnalysisResult]:
        self.calls.append([payload.topic_id for payload in payloads])
        if progress_callback is not None:
            events = [
                {
                    "event": "batch_start",
                    "batch_index": 1,
                    "batch_count": 1,
                    "batch_topic_count": len(payloads),
                    "completed_topics": 0,
                    "total_topics": len(payloads),
                },
                {
                    "event": "batch_complete",
                    "batch_index": 1,
                    "batch_count": 1,
                    "batch_topic_count": len(payloads),
                    "completed_topics": len(payloads),
                    "total_topics": len(payloads),
                },
            ]
            self.progress_events.append(events)
            for event in events:
                progress_callback(event)
        return self.plans.pop(0)


class FakeNotifier:
    def is_configured(self) -> bool:
        return False


class LinuxDoMonitorAIRetryTests(unittest.TestCase):
    def test_successful_ai_call_retries_only_previous_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "linuxdo.sqlite3"
            database = Database(database_path)
            database.initialize()

            previous_failed_payload = build_payload(100, title="历史失败请求")
            current_success_payload = build_payload(200, title="本轮成功请求")
            current_failed_payload = build_payload(300, title="本轮失败请求")

            database.upsert_topic(
                previous_failed_payload,
                TopicAnalysis(
                    primary_label="AI识别失败",
                    labels=["AI识别失败"],
                    summary="等待补偿重试。",
                    reasons=["上次请求失败。"],
                    provider="llm_failed",
                    requires_notification=False,
                ),
            )
            database.enqueue_ai_retry(previous_failed_payload, failure_reason="HTTP 500", max_retries=3)

            monitor = object.__new__(LinuxDoMonitor)
            monitor.settings = SimpleNamespace(llm_retry_limit=3)
            monitor.database = database
            monitor.classifier = FakeClassifier(
                [
                    [success_result("模型更新"), failure_result("timeout")],
                    [success_result("Codex技巧")],
                ]
            )
            monitor.notifier = FakeNotifier()
            monitor.refresh_classifier = lambda: None
            monitor.refresh_notifier = lambda: None

            monitor._store_payloads(
                [current_success_payload, current_failed_payload],
                previous_last_seen_topic_id=None,
            )

            self.assertEqual(
                monitor.classifier.calls,
                [[200, 300], [100]],
            )
            self.assertEqual(database.list_pending_ai_retry_topic_ids(), [300])

            conn = sqlite3.connect(database_path)
            try:
                retry_rows = conn.execute(
                    """
                    SELECT topic_id, status, retry_count
                    FROM ai_retry_queue
                    ORDER BY topic_id ASC
                    """
                ).fetchall()
                topic_rows = conn.execute(
                    """
                    SELECT topic_id, ai_provider, ai_label
                    FROM topics
                    WHERE topic_id IN (100, 200, 300)
                    ORDER BY topic_id ASC
                    """
                ).fetchall()
            finally:
                conn.close()

            self.assertEqual(
                retry_rows,
                [
                    (100, "resolved", 0),
                    (300, "pending", 0),
                ],
            )
            self.assertEqual(
                topic_rows,
                [
                    (100, "llm:test-model", "Codex技巧"),
                    (200, "llm:test-model", "模型更新"),
                    (300, "llm_failed", "AI识别失败"),
                ],
            )

    def test_store_payloads_emits_user_facing_progress_stages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "linuxdo.sqlite3"
            database = Database(database_path)
            database.initialize()

            payloads = [
                build_payload(200, title="本轮成功请求"),
                build_payload(300, title="本轮失败请求"),
            ]
            monitor = object.__new__(LinuxDoMonitor)
            monitor.settings = SimpleNamespace(llm_retry_limit=3, llm_batch_size=10)
            monitor.database = database
            monitor.classifier = FakeClassifier([[success_result("模型更新"), failure_result("timeout")]])
            monitor.notifier = FakeNotifier()
            monitor.refresh_classifier = lambda: None
            monitor.refresh_notifier = lambda: None
            progress_events: list[dict[str, object]] = []

            monitor._store_payloads(
                payloads,
                previous_last_seen_topic_id=None,
                progress_callback=progress_events.append,
            )

            labels = [str(event["label"]) for event in progress_events]
            self.assertIn("等待 AI 响应", labels)
            self.assertIn("AI 识别进行中", labels)
            self.assertIn("写入数据库", labels)
            self.assertIn("同步完成", labels)


class FakeClient:
    def __init__(self, pages: dict[int, list[dict[str, object]]]):
        self.pages = pages
        self.requested_pages: list[int] = []

    def fetch_latest_page(self, page_number: int) -> dict[str, object]:
        self.requested_pages.append(page_number)
        return {
            "topic_list": {
                "topics": self.pages.get(page_number, []),
            }
        }


class LinuxDoMonitorPagingTests(unittest.TestCase):
    def test_collect_new_topic_summaries_continues_across_multiple_rounds(self) -> None:
        monitor = object.__new__(LinuxDoMonitor)
        monitor.settings = SimpleNamespace(
            max_pages_per_run=2,
            page_request_delay_min_seconds=0,
            page_request_delay_max_seconds=0,
            round_delay_min_seconds=0,
            round_delay_max_seconds=0,
        )
        monitor.client = FakeClient(
            {
                0: [{"id": 130}, {"id": 129}],
                1: [{"id": 128}, {"id": 127}],
                2: [{"id": 126}, {"id": 125}],
                3: [{"id": 124}, {"id": 123}],
            }
        )

        summaries = monitor._collect_new_topic_summaries(last_seen_topic_id=124, bootstrap_limit=30)

        self.assertEqual([int(item["id"]) for item in summaries], [130, 129, 128, 127, 126, 125])
        self.assertEqual(monitor.client.requested_pages, [0, 1, 2, 3])


class LinuxDoMonitorImportTests(unittest.TestCase):
    def test_bridge_mode_does_not_import_discourse_client(self) -> None:
        database_mock = SimpleNamespace(initialize=lambda: None)
        ai_manager_mock = SimpleNamespace(load_config=lambda: {})
        notification_manager_mock = SimpleNamespace(load_config=lambda: {})

        with patch("linuxdoscanner.service.Database", return_value=database_mock):
            with patch("linuxdoscanner.service.AIConfigManager", return_value=ai_manager_mock):
                with patch("linuxdoscanner.service.NotificationConfigManager", return_value=notification_manager_mock):
                    with patch("linuxdoscanner.service.TopicClassifier", return_value=SimpleNamespace()):
                        with patch("linuxdoscanner.service.NotificationDispatcher", return_value=SimpleNamespace()):
                            with patch("linuxdoscanner.service.importlib.import_module") as import_module_mock:
                                monitor = LinuxDoMonitor(
                                    SimpleNamespace(database_path=Path("dummy.sqlite3")),
                                    enable_client=False,
                                )

        import_module_mock.assert_not_called()
        self.assertIsNone(monitor.session_manager)
        self.assertIsNone(monitor.client)


class LinuxDoMonitorStreamingTests(unittest.TestCase):
    def test_ingest_topic_documents_flushes_ai_requests_by_batch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "linuxdo.sqlite3"
            database = Database(database_path)
            database.initialize()

            documents = [
                build_topic_document(305, title="topic-305"),
                build_topic_document(304, title="topic-304"),
                build_topic_document(303, title="topic-303"),
                build_topic_document(302, title="topic-302"),
                build_topic_document(301, title="topic-301"),
            ]

            monitor = object.__new__(LinuxDoMonitor)
            monitor.settings = SimpleNamespace(
                base_url="https://linux.do",
                llm_retry_limit=3,
                llm_batch_size=2,
            )
            monitor.database = database
            monitor.classifier = FakeClassifier(
                [
                    [success_result("模型更新"), success_result("工具发布")],
                    [success_result("教程攻略"), success_result("资源分享")],
                    [success_result("Codex技巧")],
                ]
            )
            monitor.notifier = FakeNotifier()
            monitor.refresh_classifier = lambda: None
            monitor.refresh_notifier = lambda: None

            stored = monitor.ingest_topic_documents(
                documents,
                category_map={11: "搞七捻三"},
            )

            self.assertEqual(
                monitor.classifier.calls,
                [[305, 304], [303, 302], [301]],
            )
            self.assertEqual([payload.topic_id for payload in stored], [305, 304, 303, 302, 301])
            self.assertEqual(database.get_last_seen_topic_id(), 305)

    def test_ingest_topic_documents_can_skip_last_seen_commit_for_streaming_batches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "linuxdo.sqlite3"
            database = Database(database_path)
            database.initialize()

            documents = [
                build_topic_document(305, title="topic-305"),
                build_topic_document(304, title="topic-304"),
            ]

            monitor = object.__new__(LinuxDoMonitor)
            monitor.settings = SimpleNamespace(
                base_url="https://linux.do",
                llm_retry_limit=3,
                llm_batch_size=2,
            )
            monitor.database = database
            monitor.classifier = FakeClassifier([[success_result("模型更新"), success_result("工具发布")]])
            monitor.notifier = FakeNotifier()
            monitor.refresh_classifier = lambda: None
            monitor.refresh_notifier = lambda: None
            progress_events: list[dict[str, object]] = []

            stored = monitor.ingest_topic_documents(
                documents,
                category_map={11: "搞七捻三"},
                progress_callback=progress_events.append,
                update_last_seen=False,
                emit_completion_event=False,
            )

            self.assertEqual([payload.topic_id for payload in stored], [305, 304])
            self.assertIsNone(database.get_last_seen_topic_id())
            self.assertNotIn("同步完成", [str(event["label"]) for event in progress_events])

    def test_ingest_topic_documents_updates_last_seen_only_after_all_batches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "linuxdo.sqlite3"
            database = Database(database_path)
            database.initialize()

            documents = [
                build_topic_document(305, title="topic-305"),
                build_topic_document(304, title="topic-304"),
                build_topic_document(303, title="topic-303"),
            ]

            monitor = object.__new__(LinuxDoMonitor)
            monitor.settings = SimpleNamespace(
                base_url="https://linux.do",
                llm_retry_limit=3,
                llm_batch_size=2,
            )
            monitor.database = database
            monitor.classifier = FakeClassifier([[success_result("模型更新"), success_result("工具发布")]])
            monitor.notifier = FakeNotifier()
            monitor.refresh_classifier = lambda: None
            monitor.refresh_notifier = lambda: None

            call_count = 0

            def fake_store_payload_batch(payloads, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    return SimpleNamespace(
                        max_topic_id=max(payload.topic_id for payload in payloads),
                        saw_successful_ai_request=True,
                    )
                raise RuntimeError("boom")

            monitor._store_payload_batch = fake_store_payload_batch

            with self.assertRaisesRegex(RuntimeError, "boom"):
                monitor.ingest_topic_documents(
                    documents,
                    category_map={11: "搞七捻三"},
                )

            self.assertIsNone(database.get_last_seen_topic_id())


if __name__ == "__main__":
    unittest.main()
