from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from linuxdoscanner.storage import Database


LEGACY_TOPICS_TABLE_SQL = """
CREATE TABLE topics (
    topic_id INTEGER PRIMARY KEY,
    slug TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    category_id INTEGER,
    category_name TEXT,
    tags_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT,
    last_posted_at TEXT,
    excerpt TEXT,
    author_username TEXT,
    author_display_name TEXT,
    author_avatar_url TEXT,
    first_post_html TEXT,
    topic_image_url TEXT,
    image_urls_json TEXT NOT NULL DEFAULT '[]',
    external_links_json TEXT NOT NULL DEFAULT '[]',
    reply_count INTEGER,
    like_count INTEGER,
    view_count INTEGER,
    word_count INTEGER,
    access_level TEXT NOT NULL DEFAULT 'public',
    raw_json TEXT NOT NULL DEFAULT '{}',
    discovered_at TEXT NOT NULL DEFAULT '2026-03-30T00:00:00+00:00',
    fetched_at TEXT NOT NULL DEFAULT '2026-03-30T00:00:00+00:00',
    ai_provider TEXT,
    ai_label TEXT,
    ai_summary TEXT,
    ai_confidence REAL,
    ai_reasons_json TEXT NOT NULL DEFAULT '[]',
    ai_labels_json TEXT NOT NULL DEFAULT '[]',
    ai_confidence_json TEXT NOT NULL DEFAULT '{}',
    clickbait_risk TEXT NOT NULL DEFAULT 'low',
    requires_notification INTEGER NOT NULL DEFAULT 0,
    notification_sent_at TEXT
);
"""


class DatabaseSchemaMigrationTests(unittest.TestCase):
    def test_initialize_rebuilds_topics_table_without_legacy_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "linuxdo.sqlite3"
            conn = sqlite3.connect(database_path)
            try:
                conn.executescript(LEGACY_TOPICS_TABLE_SQL)
                conn.execute(
                    """
                    INSERT INTO topics (
                        topic_id, slug, title, url, category_name, tags_json,
                        fetched_at, ai_provider, ai_label, ai_summary,
                        ai_reasons_json, ai_labels_json, requires_notification
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        1846989,
                        "chatgpt-workspace-exit",
                        "退出 ChatGPT workspace 的方法",
                        "https://linux.do/t/topic/1846989",
                        "搞七捻三",
                        '["AI相关", "Codex技巧"]',
                        "2026-03-30T00:00:00+00:00",
                        "llm:gpt-5-nano",
                        "Codex技巧",
                        "正文给出了可执行退出步骤。",
                        '["包含明确操作路径"]',
                        '["AI相关", "Codex技巧"]',
                        1,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

            database = Database(database_path)
            database.initialize()

            conn = sqlite3.connect(database_path)
            try:
                columns = [row[1] for row in conn.execute("PRAGMA table_info(topics)").fetchall()]
                row = conn.execute(
                    """
                    SELECT topic_id, title, fetched_at, ai_label, requires_notification
                    FROM topics
                    WHERE topic_id = ?
                    """,
                    (1846989,),
                ).fetchone()
            finally:
                conn.close()

            self.assertNotIn("raw_json", columns)
            self.assertNotIn("excerpt", columns)
            self.assertNotIn("discovered_at", columns)
            self.assertNotIn("ai_confidence", columns)
            self.assertNotIn("ai_confidence_json", columns)
            self.assertNotIn("clickbait_risk", columns)
            self.assertIn("fetched_at", columns)
            self.assertIsNotNone(row)
            self.assertEqual(row[0], 1846989)
            self.assertEqual(row[1], "退出 ChatGPT workspace 的方法")
            self.assertEqual(row[2], "2026-03-30T00:00:00+00:00")
            self.assertEqual(row[3], "Codex技巧")
            self.assertEqual(row[4], 1)


if __name__ == "__main__":
    unittest.main()
