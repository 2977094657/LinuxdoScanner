from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def normalize_topic_tags(value: Any) -> list[str]:
    items = value if isinstance(value, list) else [value]
    tags: list[str] = []
    seen: set[str] = set()
    for item in items:
        normalized = ""
        if isinstance(item, str):
            normalized = item.strip()
        elif isinstance(item, dict):
            for key in ("name", "text", "slug", "label", "value"):
                candidate = item.get(key)
                if candidate is None:
                    continue
                normalized = str(candidate).strip()
                if normalized:
                    break
            if not normalized and item.get("id") is not None:
                normalized = str(item["id"]).strip()
        elif item is not None:
            normalized = str(item).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        tags.append(normalized)
    return tags


@dataclass(slots=True)
class TopicPayload:
    topic_id: int
    slug: str
    title: str
    url: str
    category_id: int | None = None
    category_name: str | None = None
    tags: list[str] = field(default_factory=list)
    created_at: str | None = None
    last_posted_at: str | None = None
    author_username: str | None = None
    author_display_name: str | None = None
    author_avatar_url: str | None = None
    first_post_html: str | None = None
    content_text: str | None = None
    topic_image_url: str | None = None
    image_urls: list[str] = field(default_factory=list)
    external_links: list[str] = field(default_factory=list)
    reply_count: int | None = None
    like_count: int | None = None
    view_count: int | None = None
    word_count: int | None = None
    access_level: str = "public"

    def __post_init__(self) -> None:
        self.tags = normalize_topic_tags(self.tags)


@dataclass(slots=True)
class TopicAnalysis:
    primary_label: str
    summary: str
    labels: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    provider: str = "llm"
    requires_notification: bool = False

    @property
    def label(self) -> str:
        return self.primary_label
