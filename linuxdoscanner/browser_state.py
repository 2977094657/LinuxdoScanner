from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

try:
    import browser_cookie3
except Exception:  # pragma: no cover - optional at runtime
    browser_cookie3 = None

from .settings import Settings


LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class BrowserProfile:
    browser: str
    executable_path: str | None
    user_data_dir: Path
    profile_name: str
    local_state_path: Path


class BrowserProfileError(RuntimeError):
    """Raised when the current browser profile cannot be reused or mirrored."""


MirrorMode = Literal["full", "lightweight"]


def _candidate_roots() -> list[tuple[str, Path, str | None]]:
    local_app_data = Path(os.environ.get("LOCALAPPDATA", ""))
    return [
        (
            "chrome",
            local_app_data / "Google" / "Chrome" / "User Data",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        ),
        (
            "edge",
            local_app_data / "Microsoft" / "Edge" / "User Data",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        ),
    ]


def detect_browser_profile(settings: Settings) -> BrowserProfile | None:
    preferred = settings.browser_cookie_source.lower()
    if preferred in {"off", "none", "disabled"}:
        return None
    candidates = _candidate_roots()
    if preferred in {"chrome", "edge"}:
        candidates = [item for item in candidates if item[0] == preferred]

    for browser, root, executable in candidates:
        if not root.exists():
            continue
        local_state_path = root / "Local State"
        if not local_state_path.exists():
            continue
        try:
            local_state = json.loads(local_state_path.read_text(encoding="utf-8"))
        except Exception:
            continue

        profile_section = local_state.get("profile", {})
        profile_name = (
            settings.browser_profile_name
            or profile_section.get("last_used")
            or next(iter(profile_section.get("info_cache", {}) or {}), None)
            or "Default"
        )
        profile_dir = root / profile_name
        if not profile_dir.exists():
            continue
        return BrowserProfile(
            browser=browser,
            executable_path=executable if executable and Path(executable).exists() else None,
            user_data_dir=root,
            profile_name=profile_name,
            local_state_path=local_state_path,
        )
    return None


def _jar_to_cookie_map(cookie_jar) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for cookie in cookie_jar:
        cookies[cookie.name] = cookie.value
    return cookies


def load_domain_cookies(settings: Settings, domain_name: str) -> dict[str, str]:
    if browser_cookie3 is None:
        return {}

    profile = detect_browser_profile(settings)
    if profile is None:
        return {}

    loaders: dict[str, Callable[..., object]] = {
        "chrome": browser_cookie3.chrome,
        "edge": browser_cookie3.edge,
    }
    loader = loaders.get(profile.browser)
    if loader is None:
        return {}

    try:
        jar = loader(domain_name=domain_name)
        cookies = _jar_to_cookie_map(jar)
        if cookies:
            LOGGER.info(
                "Loaded %s cookies for %s from %s profile %s.",
                len(cookies),
                domain_name,
                profile.browser,
                profile.profile_name,
            )
        return cookies
    except Exception as exc:
        LOGGER.info(
            "Could not load cookies from %s profile %s: %s",
            profile.browser,
            profile.profile_name,
            exc,
        )
        return {}


def build_managed_debug_profile(
    settings: Settings,
    status_callback: Callable[[str], None] | None = None,
    mode: MirrorMode = "full",
) -> BrowserProfile:
    profile = detect_browser_profile(settings)
    if profile is None:
        raise BrowserProfileError("未检测到可复用的 Chrome/Edge profile。")

    if is_browser_running(profile.browser):
        raise BrowserProfileError("检测到目标浏览器仍在运行，请先完全关闭浏览器后再重试。")

    target_root = settings.browser_debug_profile_dir / profile.browser
    if target_root.exists():
        _notify(status_callback, f"清理旧镜像目录: {target_root}")
        shutil.rmtree(target_root)
    target_root.mkdir(parents=True, exist_ok=True)

    try:
        if mode == "full":
            _notify(status_callback, "开始完整镜像当前浏览器用户目录，这会保留更多登录态和扩展状态。")
            _copy_full_user_data_dir(
                source_root=profile.user_data_dir,
                target_root=target_root,
                status_callback=status_callback,
            )
        else:
            _notify(status_callback, f"复制根级状态文件到镜像目录: {target_root}")
            _copy_lightweight_profile(
                source_root=profile.user_data_dir,
                profile_name=profile.profile_name,
                target_root=target_root,
                status_callback=status_callback,
            )
    except Exception as exc:
        raise BrowserProfileError(
            "复制当前浏览器 profile 失败。请先完全关闭浏览器后再重试。"
        ) from exc

    _notify(status_callback, "镜像目录准备完成。")

    return BrowserProfile(
        browser=profile.browser,
        executable_path=profile.executable_path,
        user_data_dir=target_root,
        profile_name=profile.profile_name,
        local_state_path=target_root / "Local State",
    )


def is_browser_running(browser: str) -> bool:
    image_name = {
        "chrome": "chrome.exe",
        "edge": "msedge.exe",
    }.get(browser)
    if image_name is None:
        return False

    result = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {image_name}"],
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    stdout = (result.stdout or "").lower()
    return image_name.lower() in stdout


def _ignore_runtime_locks(_: str, names: list[str]) -> set[str]:
    blocked = {
        "SingletonCookie",
        "SingletonLock",
        "SingletonSocket",
        "LOCK",
        "lockfile",
        "DevToolsActivePort",
        "chrome_debug.log",
    }
    return {name for name in names if name in blocked or name.startswith("Singleton")}


def _notify(callback: Callable[[str], None] | None, message: str) -> None:
    if callback is not None:
        callback(message)


def _copy_lightweight_profile(
    source_root: Path,
    profile_name: str,
    target_root: Path,
    status_callback: Callable[[str], None] | None,
) -> None:
    root_files = ["Local State"]
    profile_files = ["Preferences", "Secure Preferences", "Bookmarks", "History", "Visited Links"]
    profile_dirs = ["Network", "Local Storage", "Session Storage"]

    for file_name in root_files:
        src = source_root / file_name
        if src.exists():
            shutil.copy2(src, target_root / file_name)

    target_profile_dir = target_root / profile_name
    target_profile_dir.mkdir(parents=True, exist_ok=True)
    source_profile_dir = source_root / profile_name

    for file_name in profile_files:
        src = source_profile_dir / file_name
        if src.exists():
            _notify(status_callback, f"复制文件: {file_name}")
            shutil.copy2(src, target_profile_dir / file_name)

    for dir_name in profile_dirs:
        src = source_profile_dir / dir_name
        dst = target_profile_dir / dir_name
        if src.exists():
            _notify(status_callback, f"复制目录: {dir_name}")
            shutil.copytree(src, dst, dirs_exist_ok=True, ignore=_ignore_runtime_locks)


def _copy_full_user_data_dir(
    source_root: Path,
    target_root: Path,
    status_callback: Callable[[str], None] | None,
) -> None:
    plan = _build_copy_plan(source_root)
    total_files = len(plan)
    total_bytes = sum(size for _, _, size in plan)
    _notify(
        status_callback,
        f"将复制 {total_files} 个文件，约 {total_bytes / 1024 / 1024:.1f} MB。",
    )

    copied_files = 0
    copied_bytes = 0
    last_reported_percent = -1

    for src, relative, size in plan:
        dst = target_root / relative
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied_files += 1
        copied_bytes += size
        percent = int((copied_bytes / total_bytes) * 100) if total_bytes else 100
        if percent >= last_reported_percent + 5 or copied_files == total_files:
            _notify(
                status_callback,
                f"复制进度: {percent}% ({copied_files}/{total_files} 文件, {copied_bytes / 1024 / 1024:.1f}/{total_bytes / 1024 / 1024:.1f} MB)",
            )
            last_reported_percent = percent


def _build_copy_plan(source_root: Path) -> list[tuple[Path, Path, int]]:
    plan: list[tuple[Path, Path, int]] = []
    for src in source_root.rglob("*"):
        if not src.is_file():
            continue
        relative = src.relative_to(source_root)
        if _should_skip_relative(relative):
            continue
        plan.append((src, relative, src.stat().st_size))
    return plan


def _should_skip_relative(relative: Path) -> bool:
    parts = relative.parts
    if any(
        part in {
            "SingletonCookie",
            "SingletonLock",
            "SingletonSocket",
            "DevToolsActivePort",
        }
        for part in parts
    ):
        return True

    name = relative.name
    if name in {"LOCK", "lockfile", "chrome_debug.log"}:
        return True
    if name.startswith("Singleton"):
        return True
    return False
