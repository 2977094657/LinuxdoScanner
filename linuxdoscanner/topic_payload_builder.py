from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlparse

from .models import TopicPayload, normalize_topic_tags


def strip_html(html: str | None) -> str:
    if not html:
        return ""
    text = html
    for marker in ("<br>", "<br/>", "<br />", "</p>", "</div>", "</li>"):
        text = text.replace(marker, "\n")
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ")
    text = text.replace("&amp;", "&")
    text = text.replace("&#39;", "'")
    text = text.replace("&quot;", '"')
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_asset_url(base_url: str, value: str | None) -> str | None:
    if not value:
        return None
    normalized = urljoin(f"{base_url}/", value)
    if normalized.startswith("javascript:") or normalized.startswith("mailto:") or normalized.endswith("#"):
        return None
    return normalized


def normalize_avatar_url(base_url: str, avatar_template: str | None) -> str | None:
    if not avatar_template:
        return None
    return normalize_asset_url(base_url, avatar_template.replace("{size}", "96"))


def unique_strings(values: list[str | None]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value:
            continue
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def is_internal_url(base_url: str, url: str) -> bool:
    base_host = urlparse(base_url).netloc.lower()
    host = urlparse(url).netloc.lower()
    if not host:
        return True
    return host == base_host or host.endswith(f".{base_host}")


def extract_first_post_assets(base_url: str, html: str | None) -> tuple[list[str], list[str]]:
    if not html:
        return [], []

    hrefs = [
        normalize_asset_url(base_url, match)
        for match in re.findall(r"""href=["']([^"']+)["']""", html, flags=re.IGNORECASE)
    ]
    image_sources = [
        normalize_asset_url(base_url, match)
        for match in re.findall(r"""<img[^>]+src=["']([^"']+)["']""", html, flags=re.IGNORECASE)
    ]
    lightbox_targets = [
        normalize_asset_url(base_url, match)
        for match in re.findall(r"""data-download-href=["']([^"']+)["']""", html, flags=re.IGNORECASE)
    ]

    image_urls = unique_strings(image_sources + lightbox_targets + [url for url in hrefs if url and "/uploads/" in url])
    external_links = unique_strings([url for url in hrefs if url and not is_internal_url(base_url, url)])
    return image_urls, external_links


def parse_access_level(category_name: str | None) -> str:
    if not category_name:
        return "public"
    match = re.search(r",\s*(Lv[1-3])\s*$", category_name, flags=re.IGNORECASE)
    if not match:
        return "public"
    return match.group(1).lower()


def build_topic_payload(
    base_url: str,
    summary: dict[str, Any],
    detail: dict[str, Any] | None,
    category_map: dict[int, str],
) -> TopicPayload:
    posts = detail.get("post_stream", {}).get("posts", []) if detail else []
    first_post = posts[0] if posts else {}
    first_post_html = first_post.get("cooked")
    image_urls, external_links = extract_first_post_assets(base_url, first_post_html)
    topic_image_url = normalize_asset_url(base_url, (detail or {}).get("image_url") or summary.get("image_url"))
    image_urls = unique_strings([topic_image_url, *image_urls])
    slug = summary["slug"]
    topic_id = int(summary["id"])
    category_name = category_map.get(summary.get("category_id"))
    return TopicPayload(
        topic_id=topic_id,
        slug=slug,
        title=summary["title"],
        url=urljoin(f"{base_url}/", f"t/{slug}/{topic_id}"),
        category_id=summary.get("category_id"),
        category_name=category_name,
        tags=normalize_topic_tags(summary.get("tags")),
        created_at=summary.get("created_at"),
        last_posted_at=summary.get("last_posted_at"),
        author_username=first_post.get("username"),
        author_display_name=first_post.get("name") or first_post.get("display_username"),
        author_avatar_url=normalize_avatar_url(base_url, first_post.get("avatar_template")),
        first_post_html=first_post_html,
        content_text=strip_html(first_post_html) or summary.get("excerpt") or summary["title"],
        topic_image_url=topic_image_url,
        image_urls=image_urls,
        external_links=external_links,
        reply_count=(detail or {}).get("reply_count") or summary.get("reply_count"),
        like_count=(detail or {}).get("like_count") or summary.get("like_count"),
        view_count=(detail or {}).get("views") or summary.get("views"),
        word_count=(detail or {}).get("word_count") or first_post.get("word_count"),
        access_level=parse_access_level(category_name),
    )
