from __future__ import annotations

from dataclasses import asdict, dataclass, field
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


def topic_payload_to_dict(payload: TopicPayload) -> dict[str, Any]:
    return asdict(payload)


def topic_payload_from_dict(value: dict[str, Any]) -> TopicPayload:
    payload = dict(value or {})
    return TopicPayload(
        topic_id=int(payload["topic_id"]),
        slug=str(payload["slug"]),
        title=str(payload["title"]),
        url=str(payload["url"]),
        category_id=payload.get("category_id"),
        category_name=payload.get("category_name"),
        tags=list(payload.get("tags") or []),
        created_at=payload.get("created_at"),
        last_posted_at=payload.get("last_posted_at"),
        author_username=payload.get("author_username"),
        author_display_name=payload.get("author_display_name"),
        author_avatar_url=payload.get("author_avatar_url"),
        first_post_html=payload.get("first_post_html"),
        content_text=payload.get("content_text"),
        topic_image_url=payload.get("topic_image_url"),
        image_urls=list(payload.get("image_urls") or []),
        external_links=list(payload.get("external_links") or []),
        reply_count=payload.get("reply_count"),
        like_count=payload.get("like_count"),
        view_count=payload.get("view_count"),
        word_count=payload.get("word_count"),
        access_level=str(payload.get("access_level") or "public"),
    )


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


@dataclass(slots=True)
class TopicAnalysisResult:
    analysis: TopicAnalysis
    request_succeeded: bool = False
    should_retry: bool = False
    failure_reason: str | None = None


@dataclass(slots=True)
class PendingAIRetry:
    topic_id: int
    payload: TopicPayload
    retry_count: int = 0
    max_retries: int = 3
    failure_reason: str | None = None
    status: str = "pending"
    created_at: str | None = None
    updated_at: str | None = None
    last_failed_at: str | None = None
    resolved_at: str | None = None
