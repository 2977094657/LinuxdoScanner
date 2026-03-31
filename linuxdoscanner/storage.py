from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import (
    PendingAIRetry,
    TopicAnalysis,
    TopicPayload,
    topic_payload_from_dict,
    topic_payload_to_dict,
)


TOPIC_COLUMN_DEFINITIONS: dict[str, str] = {
    "topic_id": "INTEGER PRIMARY KEY",
    "slug": "TEXT NOT NULL",
    "title": "TEXT NOT NULL",
    "url": "TEXT NOT NULL",
    "category_id": "INTEGER",
    "category_name": "TEXT",
    "tags_json": "TEXT NOT NULL DEFAULT '[]'",
    "created_at": "TEXT",
    "last_posted_at": "TEXT",
    "author_username": "TEXT",
    "author_display_name": "TEXT",
    "author_avatar_url": "TEXT",
    "first_post_html": "TEXT",
    "topic_image_url": "TEXT",
    "image_urls_json": "TEXT NOT NULL DEFAULT '[]'",
    "external_links_json": "TEXT NOT NULL DEFAULT '[]'",
    "reply_count": "INTEGER",
    "like_count": "INTEGER",
    "view_count": "INTEGER",
    "word_count": "INTEGER",
    "access_level": "TEXT NOT NULL DEFAULT 'public'",
    "fetched_at": "TEXT NOT NULL",
    "ai_provider": "TEXT",
    "ai_label": "TEXT",
    "ai_summary": "TEXT",
    "ai_reasons_json": "TEXT NOT NULL DEFAULT '[]'",
    "ai_labels_json": "TEXT NOT NULL DEFAULT '[]'",
    "requires_notification": "INTEGER NOT NULL DEFAULT 0",
    "notification_sent_at": "TEXT",
}

AI_RETRY_QUEUE_COLUMN_DEFINITIONS: dict[str, str] = {
    "topic_id": "INTEGER PRIMARY KEY",
    "payload_json": "TEXT NOT NULL",
    "failure_reason": "TEXT",
    "retry_count": "INTEGER NOT NULL DEFAULT 0",
    "max_retries": "INTEGER NOT NULL DEFAULT 3",
    "status": "TEXT NOT NULL DEFAULT 'pending'",
    "created_at": "TEXT NOT NULL",
    "updated_at": "TEXT NOT NULL",
    "last_failed_at": "TEXT NOT NULL",
    "resolved_at": "TEXT",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS crawler_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS app_config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            self._ensure_topics_table(conn)
            self._ensure_ai_retry_queue_table(conn)
            self._ensure_indexes(conn)

    def get_state(self, key: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT value FROM crawler_state WHERE key = ?",
                (key,),
            ).fetchone()
        return None if row is None else row["value"]

    def set_state(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO crawler_state (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value, utc_now()),
            )

    def get_app_config_value(self, key: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT value FROM app_config WHERE key = ?",
                (key,),
            ).fetchone()
        return None if row is None else row["value"]

    def set_app_config_value(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO app_config (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value, utc_now()),
            )

    def get_app_config_json(self, key: str) -> Any:
        raw_value = self.get_app_config_value(key)
        if not raw_value:
            return None
        return json.loads(raw_value)

    def set_app_config_json(self, key: str, value: Any) -> None:
        self.set_app_config_value(key, json.dumps(value, ensure_ascii=False))

    def get_last_seen_topic_id(self) -> int | None:
        raw_value = self.get_state("last_seen_topic_id")
        return int(raw_value) if raw_value else None

    def set_last_seen_topic_id(self, topic_id: int) -> None:
        self.set_state("last_seen_topic_id", str(topic_id))

    def clear_crawl_data(self) -> dict[str, int]:
        with self.connect() as conn:
            topic_count = conn.execute("SELECT COUNT(*) AS count FROM topics").fetchone()["count"]
            state_count = conn.execute("SELECT COUNT(*) AS count FROM crawler_state").fetchone()["count"]
            ai_retry_count = conn.execute("SELECT COUNT(*) AS count FROM ai_retry_queue").fetchone()["count"]
            conn.execute("DELETE FROM topics")
            conn.execute("DELETE FROM crawler_state")
            conn.execute("DELETE FROM ai_retry_queue")
        return {
            "deleted_topics": int(topic_count),
            "deleted_state_rows": int(state_count),
            "deleted_ai_retry_rows": int(ai_retry_count),
        }

    def upsert_topic(self, payload: TopicPayload, analysis: TopicAnalysis) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO topics (
                    topic_id, slug, title, url, category_id, category_name, tags_json,
                    created_at, last_posted_at,
                    author_username, author_display_name, author_avatar_url,
                    first_post_html, topic_image_url, image_urls_json, external_links_json,
                    reply_count, like_count, view_count, word_count, access_level,
                    fetched_at,
                    ai_provider, ai_label, ai_summary,
                    ai_reasons_json, ai_labels_json, requires_notification
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?,
                    ?, ?,
                    ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?,
                    ?, ?, ?,
                    ?, ?, ?
                )
                ON CONFLICT(topic_id) DO UPDATE SET
                    slug = excluded.slug,
                    title = excluded.title,
                    url = excluded.url,
                    category_id = excluded.category_id,
                    category_name = excluded.category_name,
                    tags_json = excluded.tags_json,
                    created_at = excluded.created_at,
                    last_posted_at = excluded.last_posted_at,
                    author_username = excluded.author_username,
                    author_display_name = excluded.author_display_name,
                    author_avatar_url = excluded.author_avatar_url,
                    first_post_html = excluded.first_post_html,
                    topic_image_url = excluded.topic_image_url,
                    image_urls_json = excluded.image_urls_json,
                    external_links_json = excluded.external_links_json,
                    reply_count = excluded.reply_count,
                    like_count = excluded.like_count,
                    view_count = excluded.view_count,
                    word_count = excluded.word_count,
                    access_level = excluded.access_level,
                    fetched_at = excluded.fetched_at,
                    ai_provider = excluded.ai_provider,
                    ai_label = excluded.ai_label,
                    ai_summary = excluded.ai_summary,
                    ai_reasons_json = excluded.ai_reasons_json,
                    ai_labels_json = excluded.ai_labels_json,
                    requires_notification = excluded.requires_notification
                """,
                (
                    payload.topic_id,
                    payload.slug,
                    payload.title,
                    payload.url,
                    payload.category_id,
                    payload.category_name,
                    json.dumps(payload.tags, ensure_ascii=False),
                    payload.created_at,
                    payload.last_posted_at,
                    payload.author_username,
                    payload.author_display_name,
                    payload.author_avatar_url,
                    payload.first_post_html,
                    payload.topic_image_url,
                    json.dumps(payload.image_urls, ensure_ascii=False),
                    json.dumps(payload.external_links, ensure_ascii=False),
                    payload.reply_count,
                    payload.like_count,
                    payload.view_count,
                    payload.word_count,
                    payload.access_level,
                    now,
                    analysis.provider,
                    analysis.primary_label,
                    analysis.summary,
                    json.dumps(analysis.reasons, ensure_ascii=False),
                    json.dumps(analysis.labels, ensure_ascii=False),
                    int(analysis.requires_notification),
                ),
            )

    def list_pending_ai_retry_topic_ids(self) -> list[int]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT topic_id
                FROM ai_retry_queue
                WHERE status = 'pending'
                  AND retry_count < max_retries
                ORDER BY created_at ASC, topic_id ASC
                """
            ).fetchall()
        return [int(row["topic_id"]) for row in rows]

    def enqueue_ai_retry(
        self,
        payload: TopicPayload,
        *,
        failure_reason: str | None,
        max_retries: int,
    ) -> None:
        now = utc_now()
        retry_limit = max(0, int(max_retries))
        status = "pending" if retry_limit > 0 else "exhausted"
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO ai_retry_queue (
                    topic_id, payload_json, failure_reason, retry_count, max_retries,
                    status, created_at, updated_at, last_failed_at, resolved_at
                )
                VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT(topic_id) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    failure_reason = excluded.failure_reason,
                    retry_count = 0,
                    max_retries = excluded.max_retries,
                    status = excluded.status,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at,
                    last_failed_at = excluded.last_failed_at,
                    resolved_at = NULL
                """,
                (
                    payload.topic_id,
                    json.dumps(topic_payload_to_dict(payload), ensure_ascii=False),
                    failure_reason,
                    retry_limit,
                    status,
                    now,
                    now,
                    now,
                ),
            )

    def get_pending_ai_retries(
        self,
        topic_ids: Iterable[int] | None = None,
        *,
        limit: int | None = None,
    ) -> list[PendingAIRetry]:
        parameters: list[Any] = []
        query = """
            SELECT *
            FROM ai_retry_queue
            WHERE status = 'pending'
              AND retry_count < max_retries
        """
        if topic_ids is not None:
            deduped_ids = [int(topic_id) for topic_id in dict.fromkeys(topic_ids)]
            if not deduped_ids:
                return []
            placeholders = ", ".join("?" for _ in deduped_ids)
            query += f"\n              AND topic_id IN ({placeholders})"
            parameters.extend(deduped_ids)
        query += "\n            ORDER BY created_at ASC, topic_id ASC"
        if limit is not None:
            query += "\n            LIMIT ?"
            parameters.append(int(limit))
        with self.connect() as conn:
            rows = conn.execute(query, parameters).fetchall()
        return [self._row_to_pending_ai_retry(row) for row in rows]

    def mark_ai_retry_succeeded(self, topic_id: int) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE ai_retry_queue
                SET status = 'resolved',
                    failure_reason = NULL,
                    updated_at = ?,
                    resolved_at = ?
                WHERE topic_id = ?
                """,
                (now, now, topic_id),
            )

    def increment_ai_retry_failure(
        self,
        payload: TopicPayload,
        *,
        failure_reason: str | None,
    ) -> PendingAIRetry | None:
        now = utc_now()
        payload_json = json.dumps(topic_payload_to_dict(payload), ensure_ascii=False)
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE ai_retry_queue
                SET payload_json = ?,
                    failure_reason = ?,
                    retry_count = retry_count + 1,
                    status = CASE
                        WHEN retry_count + 1 >= max_retries THEN 'exhausted'
                        ELSE 'pending'
                    END,
                    updated_at = ?,
                    last_failed_at = ?,
                    resolved_at = NULL
                WHERE topic_id = ?
                """,
                (
                    payload_json,
                    failure_reason,
                    now,
                    now,
                    payload.topic_id,
                ),
            )
            if cursor.rowcount <= 0:
                return None
            row = conn.execute(
                """
                SELECT *
                FROM ai_retry_queue
                WHERE topic_id = ?
                """,
                (payload.topic_id,),
            ).fetchone()
        return None if row is None else self._row_to_pending_ai_retry(row)

    def get_pending_notifications(self, limit: int = 20) -> list[sqlite3.Row]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM topics
                WHERE requires_notification = 1
                  AND notification_sent_at IS NULL
                ORDER BY created_at DESC, topic_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return list(rows)

    def mark_topics_notified(self, topic_ids: Iterable[int]) -> None:
        topic_ids = list(topic_ids)
        if not topic_ids:
            return
        placeholders = ", ".join("?" for _ in topic_ids)
        with self.connect() as conn:
            conn.execute(
                f"""
                UPDATE topics
                SET notification_sent_at = ?
                WHERE topic_id IN ({placeholders})
                """,
                (utc_now(), *topic_ids),
            )

    def _ensure_topics_table(self, conn: sqlite3.Connection) -> None:
        if not self._table_exists(conn, "topics"):
            conn.execute(self._create_topics_table_sql())
            return

        existing_columns = self._list_columns(conn, "topics")
        desired_columns = list(TOPIC_COLUMN_DEFINITIONS.keys())
        if set(existing_columns) != set(desired_columns):
            self._rebuild_topics_table(conn, existing_columns)

    def _ensure_ai_retry_queue_table(self, conn: sqlite3.Connection) -> None:
        if not self._table_exists(conn, "ai_retry_queue"):
            conn.execute(self._create_ai_retry_queue_table_sql())
            return

        existing_columns = self._list_columns(conn, "ai_retry_queue")
        desired_columns = list(AI_RETRY_QUEUE_COLUMN_DEFINITIONS.keys())
        if set(existing_columns) != set(desired_columns):
            self._rebuild_ai_retry_queue_table(conn, existing_columns)

    def _rebuild_topics_table(self, conn: sqlite3.Connection, existing_columns: list[str]) -> None:
        conn.execute("DROP TABLE IF EXISTS topics__new")
        conn.execute(self._create_topics_table_sql(table_name="topics__new"))
        shared_columns = [column for column in TOPIC_COLUMN_DEFINITIONS if column in existing_columns]
        if shared_columns:
            column_sql = ", ".join(shared_columns)
            conn.execute(
                f"""
                INSERT INTO topics__new ({column_sql})
                SELECT {column_sql}
                FROM topics
                """
            )
        conn.execute("DROP TABLE topics")
        conn.execute("ALTER TABLE topics__new RENAME TO topics")

    def _rebuild_ai_retry_queue_table(self, conn: sqlite3.Connection, existing_columns: list[str]) -> None:
        conn.execute("DROP TABLE IF EXISTS ai_retry_queue__new")
        conn.execute(self._create_ai_retry_queue_table_sql(table_name="ai_retry_queue__new"))
        shared_columns = [column for column in AI_RETRY_QUEUE_COLUMN_DEFINITIONS if column in existing_columns]
        if shared_columns:
            column_sql = ", ".join(shared_columns)
            conn.execute(
                f"""
                INSERT INTO ai_retry_queue__new ({column_sql})
                SELECT {column_sql}
                FROM ai_retry_queue
                """
            )
        conn.execute("DROP TABLE ai_retry_queue")
        conn.execute("ALTER TABLE ai_retry_queue__new RENAME TO ai_retry_queue")

    def _ensure_indexes(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_topics_requires_notification
            ON topics (requires_notification, notification_sent_at);

            CREATE INDEX IF NOT EXISTS idx_topics_created_at
            ON topics (created_at DESC);

            CREATE INDEX IF NOT EXISTS idx_ai_retry_queue_status
            ON ai_retry_queue (status, retry_count, created_at ASC);
            """
        )

    def _table_exists(self, conn: sqlite3.Connection, table_name: str) -> bool:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        return row is not None

    def _list_columns(self, conn: sqlite3.Connection, table_name: str) -> list[str]:
        return [row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()]

    def _create_topics_table_sql(self, table_name: str = "topics") -> str:
        column_sql = ",\n                    ".join(
            f"{column_name} {column_type}"
            for column_name, column_type in TOPIC_COLUMN_DEFINITIONS.items()
        )
        return f"""
                CREATE TABLE IF NOT EXISTS {table_name} (
                    {column_sql}
                );
                """

    def _create_ai_retry_queue_table_sql(self, table_name: str = "ai_retry_queue") -> str:
        column_sql = ",\n                    ".join(
            f"{column_name} {column_type}"
            for column_name, column_type in AI_RETRY_QUEUE_COLUMN_DEFINITIONS.items()
        )
        return f"""
                CREATE TABLE IF NOT EXISTS {table_name} (
                    {column_sql}
                );
                """

    def _row_to_pending_ai_retry(self, row: sqlite3.Row) -> PendingAIRetry:
        payload = topic_payload_from_dict(json.loads(row["payload_json"]))
        return PendingAIRetry(
            topic_id=int(row["topic_id"]),
            payload=payload,
            retry_count=int(row["retry_count"]),
            max_retries=int(row["max_retries"]),
            failure_reason=row["failure_reason"],
            status=str(row["status"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_failed_at=row["last_failed_at"],
            resolved_at=row["resolved_at"],
        )
