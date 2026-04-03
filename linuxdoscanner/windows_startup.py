from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from .runtime_paths import current_executable, is_frozen
from .settings import Settings


STARTUP_SCRIPT_NAME = "LinuxDoScanner Bridge.cmd"
STARTUP_SCRIPT_MARKER = "linuxdoscanner-startup"
DEFAULT_BROWSER_LAUNCH_DELAY_SECONDS = 8


@dataclass(slots=True)
class WindowsStartupStatus:
    supported: bool
    enabled: bool
    use_tray: bool
    launch_browser: bool
    browser_url: str
    browser_executable: str | None
    startup_dir: Path | None
    script_path: Path | None
    python_executable: str | None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "supported": self.supported,
            "enabled": self.enabled,
            "use_tray": self.use_tray,
            "launch_browser": self.launch_browser,
            "browser_url": self.browser_url,
            "browser_executable": self.browser_executable or "",
            "startup_dir": str(self.startup_dir) if self.startup_dir is not None else "",
            "script_path": str(self.script_path) if self.script_path is not None else "",
            "python_executable": self.python_executable or "",
            "reason": self.reason,
        }


class WindowsStartupManager:
    def __init__(
        self,
        settings: Settings,
        *,
        python_executable: str | None = None,
        appdata_dir: str | Path | None = None,
        platform_name: str | None = None,
    ) -> None:
        self.settings = settings
        self.platform_name = platform_name or os.name
        self.startup_dir = self._resolve_startup_dir(appdata_dir)
        self.script_path = self.startup_dir / STARTUP_SCRIPT_NAME if self.startup_dir is not None else None
        self.python_executable = self._resolve_python_executable(python_executable)

    def default_browser_url(self) -> str:
        return f"{self.settings.base_url.rstrip('/')}/latest?order=created"

    def status(self) -> WindowsStartupStatus:
        if self.platform_name != "nt":
            return WindowsStartupStatus(
                supported=False,
                enabled=False,
                use_tray=True,
                launch_browser=False,
                browser_url=self.default_browser_url(),
                browser_executable=self.settings.browser_executable,
                startup_dir=None,
                script_path=None,
                python_executable=self.python_executable,
                reason="当前仅支持 Windows 开机自启动。",
            )

        if self.startup_dir is None or self.script_path is None:
            return WindowsStartupStatus(
                supported=True,
                enabled=False,
                use_tray=True,
                launch_browser=False,
                browser_url=self.default_browser_url(),
                browser_executable=self.settings.browser_executable,
                startup_dir=self.startup_dir,
                script_path=self.script_path,
                python_executable=self.python_executable,
                reason="无法定位当前用户的 Startup 目录。",
            )

        enabled = self.script_path.exists()
        metadata = self._read_metadata() if enabled else None
        use_tray = bool(metadata.get("use_tray", True)) if metadata else True
        launch_browser = bool(metadata.get("launch_browser")) if metadata else False
        browser_url = self.default_browser_url()
        browser_executable = self.settings.browser_executable
        if metadata is not None:
            browser_url = self._normalize_browser_url(metadata.get("browser_url"), allow_default=True)
            metadata_browser_executable = str(metadata.get("browser_executable") or "").strip()
            if metadata_browser_executable:
                browser_executable = metadata_browser_executable

        reason = ""
        if enabled and metadata is None:
            reason = "已检测到启动脚本，但未读取到 LinuxDoScanner 元信息。"

        return WindowsStartupStatus(
            supported=True,
            enabled=enabled,
            use_tray=use_tray,
            launch_browser=launch_browser,
            browser_url=browser_url,
            browser_executable=browser_executable,
            startup_dir=self.startup_dir,
            script_path=self.script_path,
            python_executable=self.python_executable,
            reason=reason,
        )

    def install(
        self,
        *,
        use_tray: bool = True,
        launch_browser: bool,
        browser_url: str | None = None,
        browser_executable: str | None = None,
    ) -> WindowsStartupStatus:
        status = self.status()
        if not status.supported:
            raise RuntimeError(status.reason)
        if self.startup_dir is None or self.script_path is None:
            raise RuntimeError("无法定位 Startup 目录，暂时无法写入开机启动脚本。")

        normalized_browser_url = self._normalize_browser_url(browser_url, allow_default=True)
        resolved_browser_executable = self._resolve_browser_executable(browser_executable)
        if launch_browser and not resolved_browser_executable:
            raise RuntimeError(
                "未检测到 Chrome 或 Edge。请先安装浏览器，或在 config/settings.toml 的 [browser].executable 中手动指定路径。"
            )
        self._ensure_startup_launcher_available()

        self.startup_dir.mkdir(parents=True, exist_ok=True)
        self.script_path.write_text(
            self._render_script(
                use_tray=use_tray,
                launch_browser=launch_browser,
                browser_url=normalized_browser_url,
                browser_executable=resolved_browser_executable,
            ),
            encoding="utf-8-sig",
        )
        return self.status()

    def remove(self) -> WindowsStartupStatus:
        if self.script_path is not None and self.script_path.exists():
            self.script_path.unlink()
        return self.status()

    def _resolve_startup_dir(self, appdata_dir: str | Path | None) -> Path | None:
        if self.platform_name != "nt":
            return None
        raw_appdata = str(appdata_dir).strip() if appdata_dir is not None else os.getenv("APPDATA", "").strip()
        if not raw_appdata:
            return None
        return (
            Path(raw_appdata).expanduser()
            / "Microsoft"
            / "Windows"
            / "Start Menu"
            / "Programs"
            / "Startup"
        )

    def _resolve_python_executable(self, override: str | None) -> str | None:
        raw_path = (override or sys.executable or "").strip()
        if not raw_path:
            return None
        executable_path = Path(raw_path).expanduser()
        if executable_path.name.lower() == "python.exe":
            pythonw_path = executable_path.with_name("pythonw.exe")
            if pythonw_path.exists():
                executable_path = pythonw_path
        return str(executable_path.resolve(strict=False))

    def _resolve_browser_executable(self, override: str | None = None) -> str | None:
        candidate = str(override or "").strip() or self.settings.browser_executable
        if not candidate:
            return None
        browser_path = Path(candidate).expanduser()
        if not browser_path.exists():
            raise RuntimeError(f"浏览器可执行文件不存在: {browser_path}")
        return str(browser_path.resolve(strict=False))

    def _normalize_browser_url(self, browser_url: Any, *, allow_default: bool) -> str:
        raw_url = str(browser_url or "").strip()
        if not raw_url:
            if allow_default:
                return self.default_browser_url()
            raise ValueError("浏览器启动地址不能为空。")
        if not raw_url.startswith(("http://", "https://")):
            raise ValueError("浏览器启动地址必须以 http:// 或 https:// 开头。")
        return raw_url

    def _read_metadata(self) -> dict[str, Any] | None:
        if self.script_path is None or not self.script_path.exists():
            return None
        marker = f"REM {STARTUP_SCRIPT_MARKER} "
        content = self.script_path.read_text(encoding="utf-8-sig", errors="ignore")
        for line in content.splitlines():
            if not line.startswith(marker):
                continue
            try:
                payload = json.loads(line[len(marker) :].strip())
            except json.JSONDecodeError:
                return None
            return payload if isinstance(payload, dict) else None
        return None

    def _render_script(
        self,
        *,
        use_tray: bool,
        launch_browser: bool,
        browser_url: str,
        browser_executable: str | None,
    ) -> str:
        metadata = json.dumps(
            {
                "use_tray": bool(use_tray),
                "launch_browser": bool(launch_browser),
                "browser_url": browser_url,
                "browser_executable": browser_executable or "",
            },
            ensure_ascii=True,
            separators=(",", ":"),
        )
        startup_args = self._startup_command_args()
        startup_args.append("startup-run")
        if not use_tray:
            startup_args.append("--no-tray")
        if launch_browser:
            startup_args.append("--launch-browser")
            startup_args.extend(["--browser-url", browser_url])
            if browser_executable:
                startup_args.extend(["--browser-executable", browser_executable])

        return "\r\n".join(
            [
                "@echo off",
                f"REM {STARTUP_SCRIPT_MARKER} {metadata}",
                f"cd /d {subprocess.list2cmdline([str(self.settings.project_root.resolve(strict=False))])}",
                f'start "" {subprocess.list2cmdline(startup_args)}',
                "",
            ]
        )

    def _ensure_startup_launcher_available(self) -> None:
        if is_frozen():
            executable_path = current_executable()
            if not executable_path.exists():
                raise RuntimeError(f"未找到当前打包后的可执行文件: {executable_path}")
            return

        python_executable_path = Path(self.python_executable or "")
        if not python_executable_path.exists():
            raise RuntimeError(f"未找到可用于开机启动的 Python 解释器: {self.python_executable}")

        main_script_path = (self.settings.project_root / "main.py").resolve()
        if not main_script_path.exists():
            raise RuntimeError(f"未找到启动入口文件: {main_script_path}")

    def _startup_command_args(self) -> list[str]:
        if is_frozen():
            return [str(current_executable())]
        return [
            self.python_executable or "",
            str((self.settings.project_root / "main.py").resolve(strict=False)),
        ]


def _creation_flags(*, detached: bool = False, no_window: bool = False) -> int:
    flags = 0
    if detached:
        flags |= int(getattr(subprocess, "DETACHED_PROCESS", 0))
        flags |= int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    if no_window:
        flags |= int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return flags


def is_bridge_server_healthy(settings: Settings, timeout_seconds: float = 1.5) -> bool:
    host = settings.bridge_host.strip() or "127.0.0.1"
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    request = urllib.request.Request(
        f"http://{host}:{settings.bridge_port}/api/bridge/health",
        headers={"Accept": "application/json"},
    )
    if settings.bridge_token:
        request.add_header("X-LinuxDo-Bridge-Token", settings.bridge_token)
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return int(getattr(response, "status", 0)) == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def is_browser_running(browser_executable: str) -> bool:
    executable_name = Path(browser_executable).name
    if not executable_name:
        return False
    result = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {executable_name}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        check=False,
        creationflags=_creation_flags(no_window=True),
    )
    return executable_name.lower() in result.stdout.lower()


def launch_browser_process(browser_executable: str, browser_url: str) -> bool:
    if is_browser_running(browser_executable):
        logger.info("Browser is already running, skip wake-up: {}", browser_executable)
        return False

    subprocess.Popen(
        [browser_executable, browser_url],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=_creation_flags(detached=True),
    )
    logger.info("Triggered browser wake-up: {} -> {}", browser_executable, browser_url)
    return True


def launch_browser_after_delay(
    browser_executable: str,
    browser_url: str,
    *,
    delay_seconds: int = DEFAULT_BROWSER_LAUNCH_DELAY_SECONDS,
) -> None:
    try:
        if delay_seconds > 0:
            time.sleep(delay_seconds)
        launch_browser_process(browser_executable, browser_url)
    except Exception as exc:
        logger.warning("Unable to wake browser on startup: {}", exc)
