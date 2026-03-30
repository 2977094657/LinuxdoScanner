from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from .browser_state import detect_browser_profile, load_domain_cookies
from .models import TopicPayload, normalize_topic_tags
from .settings import Settings


LOGGER = logging.getLogger(__name__)


class BrowserSessionError(RuntimeError):
    """Raised when the saved browser session is missing or invalid."""


class APIAccessError(RuntimeError):
    """Raised when a JSON endpoint cannot be fetched after fallback attempts."""


@dataclass(slots=True)
class SessionMetadata:
    user_agent: str
    captured_at: str


def strip_html(html: str | None) -> str:
    if not html:
        return ""
    text = html
    for marker in ("<br>", "<br/>", "<br />", "</p>", "</div>", "</li>"):
        text = text.replace(marker, "\n")
    import re

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


class BrowserSessionManager:
    def __init__(self, settings: Settings):
        self.settings = settings

    def session_exists(self) -> bool:
        return self.settings.storage_state_path.exists() and self.settings.session_meta_path.exists()

    def load_metadata(self) -> SessionMetadata | None:
        if not self.settings.session_meta_path.exists():
            return None
        payload = json.loads(self.settings.session_meta_path.read_text(encoding="utf-8"))
        return SessionMetadata(
            user_agent=payload["user_agent"],
            captured_at=payload["captured_at"],
        )

    def save_metadata(self, metadata: SessionMetadata) -> None:
        write_session_metadata(self.settings.session_meta_path, metadata)

    def capture_login(
        self,
        wait_timeout_seconds: int | None = None,
        isolated: bool = False,
    ) -> SessionMetadata:
        wait_timeout_seconds = wait_timeout_seconds or self.settings.auth_wait_timeout_seconds
        if self.settings.browser_cdp_url:
            return self._capture_login_via_cdp(wait_timeout_seconds)

        if not isolated:
            profile = detect_browser_profile(self.settings)
            if profile is None:
                raise BrowserSessionError(
                    "未找到可复用的 Chrome/Edge profile。"
                    "可以设置 LINUXDO_BROWSER_PROFILE，或使用 `python main.py auth --isolated`。"
                )
            return self._capture_login_via_profile(profile, wait_timeout_seconds)

        return self._capture_login_via_isolated_profile(wait_timeout_seconds)

    def _capture_login_via_profile(
        self,
        profile,
        wait_timeout_seconds: int,
    ) -> SessionMetadata:
        executable_path = self.settings.browser_executable or profile.executable_path
        try:
            with sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(
                    user_data_dir=str(profile.user_data_dir),
                    executable_path=executable_path,
                    headless=False,
                    args=[
                        f"--profile-directory={profile.profile_name}",
                        "--disable-blink-features=AutomationControlled",
                        "--disable-dev-shm-usage",
                        "--no-sandbox",
                    ],
                    viewport={"width": 1440, "height": 900},
                )
                try:
                    return self._wait_for_login_and_save(context, wait_timeout_seconds)
                finally:
                    context.close()
        except Exception as exc:
            raise BrowserSessionError(
                "无法直接复用你当前的浏览器 profile。"
                "如果 Chrome/Edge 正在运行，请先关闭它再执行 `python main.py auth`，"
                "或者给浏览器开启远程调试并设置 LINUXDO_BROWSER_CDP_URL。"
            ) from exc

    def _capture_login_via_isolated_profile(self, wait_timeout_seconds: int) -> SessionMetadata:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.settings.browser_profile_dir),
                executable_path=self.settings.browser_executable,
                headless=False,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                ],
                viewport={"width": 1440, "height": 900},
            )
            try:
                return self._wait_for_login_and_save(context, wait_timeout_seconds)
            finally:
                context.close()

    def _capture_login_via_cdp(self, wait_timeout_seconds: int) -> SessionMetadata:
        cdp_ws_url = resolve_cdp_websocket_url(self.settings.browser_cdp_url)
        original_env = {
            "HTTP_PROXY": os.environ.get("HTTP_PROXY"),
            "HTTPS_PROXY": os.environ.get("HTTPS_PROXY"),
            "ALL_PROXY": os.environ.get("ALL_PROXY"),
            "NO_PROXY": os.environ.get("NO_PROXY"),
        }
        try:
            os.environ["NO_PROXY"] = "127.0.0.1,localhost"
            os.environ.pop("HTTP_PROXY", None)
            os.environ.pop("HTTPS_PROXY", None)
            os.environ.pop("ALL_PROXY", None)
            with sync_playwright() as playwright:
                browser = playwright.chromium.connect_over_cdp(cdp_ws_url)
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                return self._wait_for_login_and_save(context=context, wait_timeout_seconds=wait_timeout_seconds)
        finally:
            for key, value in original_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def _wait_for_login_and_save(
        self,
        context: BrowserContext,
        wait_timeout_seconds: int,
    ) -> SessionMetadata:
        page = context.new_page()
        page.goto(f"{self.settings.base_url}/login", wait_until="domcontentloaded")
        check_page = context.new_page()
        try:
            deadline = time.time() + wait_timeout_seconds
            while time.time() < deadline:
                if self._is_logged_in(check_page):
                    metadata = SessionMetadata(
                        user_agent=page.evaluate("() => navigator.userAgent"),
                        captured_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    )
                    context.storage_state(path=str(self.settings.storage_state_path))
                    self.save_metadata(metadata)
                    return metadata
                time.sleep(2)
            raise BrowserSessionError("Timed out waiting for manual login to complete.")
        finally:
            try:
                check_page.close()
            except Exception:
                pass
            try:
                page.close()
            except Exception:
                pass

    def _is_logged_in(self, page: Page) -> bool:
        page.goto(self.settings.base_url, wait_until="domcontentloaded")
        try:
            page.locator("#current-user").wait_for(timeout=3_000)
            return True
        except Exception:
            return False

    def open_browser_fetcher(self, use_saved_session: bool = True, headless: bool | None = None) -> "BrowserJSONFetcher":
        metadata = self.load_metadata()
        return BrowserJSONFetcher(
            settings=self.settings,
            storage_state_path=self.settings.storage_state_path if use_saved_session and self.settings.storage_state_path.exists() else None,
            user_agent=metadata.user_agent if metadata else None,
            headless=self.settings.browser_fallback_headless if headless is None else headless,
        )


class BrowserJSONFetcher:
    def __init__(
        self,
        settings: Settings,
        storage_state_path: Path | None,
        user_agent: str | None,
        headless: bool,
    ):
        self.settings = settings
        self.storage_state_path = storage_state_path
        self.user_agent = user_agent
        self.headless = headless
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._cdp_browser = None
        self._owns_browser = True
        self._owns_context = True
        self._owns_page = True
        self.logged_in = False

    def __enter__(self) -> "BrowserJSONFetcher":
        if self.settings.browser_cdp_url:
            cdp_ws_url = resolve_cdp_websocket_url(self.settings.browser_cdp_url)
            original_env = {
                "HTTP_PROXY": os.environ.get("HTTP_PROXY"),
                "HTTPS_PROXY": os.environ.get("HTTPS_PROXY"),
                "ALL_PROXY": os.environ.get("ALL_PROXY"),
                "NO_PROXY": os.environ.get("NO_PROXY"),
            }
            try:
                os.environ["NO_PROXY"] = "127.0.0.1,localhost"
                os.environ.pop("HTTP_PROXY", None)
                os.environ.pop("HTTPS_PROXY", None)
                os.environ.pop("ALL_PROXY", None)
                self._playwright = sync_playwright().start()
                self._cdp_browser = self._playwright.chromium.connect_over_cdp(cdp_ws_url)
            finally:
                for key, value in original_env.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
            self._owns_browser = False
            if self._cdp_browser.contexts:
                self._context = self._cdp_browser.contexts[0]
                self._owns_context = False
            else:
                self._context = self._cdp_browser.new_context()
        else:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(
                executable_path=self.settings.browser_executable,
                headless=self.headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                ],
            )
            context_kwargs: dict[str, Any] = {}
            if self.storage_state_path is not None:
                context_kwargs["storage_state"] = str(self.storage_state_path)
            if self.user_agent:
                context_kwargs["user_agent"] = self.user_agent
            self._context = self._browser.new_context(**context_kwargs)
        self._page = self._context.new_page()
        self._page.goto(self.settings.base_url, wait_until="domcontentloaded")
        self._wait_for_challenge_clearance()
        self.logged_in = self._check_logged_in()
        self._log_login_status()
        self._persist_session_if_logged_in()
        self._enforce_login_requirement()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._page is not None and self._owns_page:
            try:
                self._page.close()
            except Exception:
                pass
        if self._context is not None and self._owns_context:
            try:
                self._context.close()
            except Exception:
                pass
        if self._browser is not None and self._owns_browser:
            self._browser.close()
        if self._playwright is not None:
            self._playwright.stop()

    def _wait_for_challenge_clearance(self) -> None:
        if self._page is None:
            return
        for _ in range(30):
            title = self._page.title()
            try:
                body_text = self._page.locator("body").inner_text(timeout=1_000)
            except Exception:
                body_text = ""
            if "Just a moment" not in title and "Checking your browser" not in body_text:
                return
            time.sleep(1)

    def _log_login_status(self) -> None:
        if self.logged_in:
            LOGGER.info("Browser session is logged in to linux.do.")
        else:
            LOGGER.warning(
                "Browser session is not logged in to linux.do. Public topics can still be crawled, "
                "but permissioned topics require logging in within the current debug browser or running `python main.py auth` while CDP is connected."
            )

    def _check_logged_in(self) -> bool:
        if self._page is None:
            return False
        try:
            return self._page.locator("#current-user").count() > 0
        except Exception:
            return False

    def _persist_session_if_logged_in(self) -> None:
        if not self.logged_in or self._page is None or self._context is None:
            return
        try:
            metadata = SessionMetadata(
                user_agent=self._page.evaluate("() => navigator.userAgent"),
                captured_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )
            self._context.storage_state(path=str(self.settings.storage_state_path))
            write_session_metadata(self.settings.session_meta_path, metadata)
        except Exception as exc:
            LOGGER.warning("Failed to persist logged-in browser session: %s", exc)

    def _enforce_login_requirement(self) -> None:
        if self.settings.require_login and not self.logged_in:
            raise BrowserSessionError(
                "当前浏览器未登录 linux.do，且已启用 LINUXDO_REQUIRE_LOGIN=1。"
                "请先在调试浏览器中执行 `python main.py auth`，再重新运行抓取。"
            )

    def fetch_json(self, path_or_url: str) -> dict[str, Any]:
        if self._page is None:
            raise RuntimeError("BrowserJSONFetcher must be used as a context manager.")
        url = normalize_url(self.settings.base_url, path_or_url)
        result = self._page.evaluate(
            """
            async ({ url }) => {
              const response = await fetch(url, {
                credentials: "include",
                headers: {
                  "accept": "application/json, text/plain, */*"
                }
              });
              const text = await response.text();
              return { status: response.status, text };
            }
            """,
            {"url": url},
        )
        if result["status"] != 200:
            raise APIAccessError(f"Browser fetch failed with status {result['status']} for {url}")
        return json.loads(result["text"])


def normalize_url(base_url: str, path_or_url: str) -> str:
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        return path_or_url
    return urljoin(f"{base_url}/", path_or_url.lstrip("/"))


def resolve_cdp_websocket_url(endpoint: str) -> str:
    if endpoint.startswith("ws://") or endpoint.startswith("wss://"):
        return endpoint

    try:
        with httpx.Client(timeout=10.0, trust_env=False) as client:
            response = client.get(f"{endpoint.rstrip('/')}/json/version")
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as exc:
        raise BrowserSessionError(
            f"无法连接到 CDP 调试浏览器 `{endpoint}`。"
            "请确认调试浏览器仍然开着，并且 9222 端口可访问。"
        ) from exc

    ws_url = payload.get("webSocketDebuggerUrl")
    if not ws_url:
        raise BrowserSessionError("未从 CDP 端点拿到 webSocketDebuggerUrl。")
    return ws_url


def load_cookies(storage_state_path: Path) -> dict[str, str]:
    if not storage_state_path.exists():
        return {}
    payload = json.loads(storage_state_path.read_text(encoding="utf-8"))
    cookies: dict[str, str] = {}
    for cookie in payload.get("cookies", []):
        cookies[cookie["name"]] = cookie["value"]
    return cookies


def write_session_metadata(path: Path, metadata: SessionMetadata) -> None:
    path.write_text(
        json.dumps(
            {
                "user_agent": metadata.user_agent,
                "captured_at": metadata.captured_at,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


class DiscourseAPIClient:
    def __init__(self, settings: Settings, session_manager: BrowserSessionManager):
        self.settings = settings
        self.session_manager = session_manager
        metadata = session_manager.load_metadata()
        headers = {
            "accept": "application/json, text/plain, */*",
            "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
            "referer": f"{settings.base_url}/",
            "user-agent": metadata.user_agent if metadata else (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
            ),
        }
        cookies = load_cookies(settings.storage_state_path)
        if not settings.browser_cdp_url:
            browser_cookies = load_domain_cookies(settings, domain_name="linux.do")
            cookies.update(browser_cookies)
        self.http = httpx.Client(
            headers=headers,
            cookies=cookies,
            follow_redirects=True,
            timeout=30.0,
        )
        self._prefer_browser = False
        self._browser_fetcher: BrowserJSONFetcher | None = None
        self.category_map: dict[int, str] = {}

    def close(self) -> None:
        if self._browser_fetcher is not None:
            self._browser_fetcher.__exit__(None, None, None)
            self._browser_fetcher = None
        self.http.close()

    def _looks_like_challenge(self, response: httpx.Response) -> bool:
        body = response.text[:500]
        return response.status_code == 403 and (
            "Just a moment" in body or "cf-mitigated" in response.headers.get("cf-mitigated", "").lower()
        )

    def _ensure_browser_fetcher(self) -> BrowserJSONFetcher:
        if self._browser_fetcher is None:
            self._browser_fetcher = self.session_manager.open_browser_fetcher(
                use_saved_session=self.session_manager.session_exists(),
                headless=self.settings.browser_fallback_headless,
            )
            self._browser_fetcher.__enter__()
        return self._browser_fetcher

    def _restart_browser_fetcher(self, headless: bool) -> BrowserJSONFetcher:
        if self._browser_fetcher is not None:
            self._browser_fetcher.__exit__(None, None, None)
        self._browser_fetcher = self.session_manager.open_browser_fetcher(
            use_saved_session=self.session_manager.session_exists(),
            headless=headless,
        )
        self._browser_fetcher.__enter__()
        return self._browser_fetcher

    def fetch_json(self, path_or_url: str) -> dict[str, Any]:
        url = normalize_url(self.settings.base_url, path_or_url)
        if not self._prefer_browser:
            response = self.http.get(url)
            if response.status_code == 200 and "json" in response.headers.get("content-type", ""):
                return response.json()
            if self._looks_like_challenge(response):
                self._prefer_browser = True
            else:
                raise APIAccessError(f"HTTP fetch failed with status {response.status_code} for {url}")
        try:
            return self._ensure_browser_fetcher().fetch_json(url)
        except APIAccessError:
            if self.settings.browser_fallback_headless:
                return self._restart_browser_fetcher(headless=False).fetch_json(url)
            raise

    def load_categories(self) -> dict[int, str]:
        if self.category_map:
            return self.category_map
        data = self.fetch_json("/site.json")
        self.category_map = {item["id"]: item["name"] for item in data.get("categories", [])}
        return self.category_map

    def fetch_latest_page(self, page_number: int = 0) -> dict[str, Any]:
        suffix = f"/latest.json?order=created&page={page_number}" if page_number else "/latest.json?order=created"
        return self.fetch_json(suffix)

    def fetch_topic_detail(self, topic_id: int, slug: str) -> dict[str, Any]:
        return self.fetch_json(f"/t/{slug}/{topic_id}.json")

    def probe(self) -> dict[str, str]:
        results: dict[str, str] = {}
        site_response = self.http.get(normalize_url(self.settings.base_url, "/site.json"))
        results["site.json_raw"] = str(site_response.status_code)
        latest_response = self.http.get(normalize_url(self.settings.base_url, "/latest.json"))
        results["latest.json_raw"] = str(latest_response.status_code)
        try:
            browser_data = self.fetch_json("/latest.json")
            results["latest.json_browser"] = str(len(browser_data.get("topic_list", {}).get("topics", [])))
            if self._browser_fetcher is not None:
                results["browser_logged_in"] = "yes" if self._browser_fetcher.logged_in else "no"
            results["saved_session"] = "yes" if self.session_manager.session_exists() else "no"
        except Exception as exc:
            results["latest.json_browser"] = f"error:{exc}"
        return results


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
        url=normalize_url(base_url, f"/t/{slug}/{topic_id}"),
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
