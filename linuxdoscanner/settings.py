from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def env_bool(name: str, default: bool = False) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def detect_browser_executable() -> str | None:
    candidates = [
        os.getenv("LINUXDO_BROWSER_EXECUTABLE"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def detect_lark_cli_executable() -> str | None:
    candidates = [
        os.getenv("LARK_CLI_PATH"),
        r"C:\Program Files\nodejs\lark-cli.cmd",
        r"C:\Program Files\nodejs\lark-cli",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


@dataclass(slots=True)
class Settings:
    base_url: str
    data_dir: Path
    database_path: Path
    browser_profile_dir: Path
    browser_debug_profile_dir: Path
    storage_state_path: Path
    session_meta_path: Path
    browser_executable: str | None
    browser_cookie_source: str
    browser_profile_name: str | None
    browser_cdp_url: str | None
    require_login: bool
    bridge_host: str
    bridge_port: int
    bridge_token: str | None
    poll_interval_seconds: int
    bootstrap_limit: int
    max_pages_per_run: int
    browser_fallback_headless: bool
    auth_wait_timeout_seconds: int
    llm_batch_size: int
    llm_retry_limit: int
    openai_api_key: str | None
    openai_base_url: str | None
    openai_model: str | None
    smtp_host: str | None
    smtp_port: int
    smtp_username: str | None
    smtp_password: str | None
    smtp_sender: str | None
    smtp_recipient: str | None
    smtp_use_tls: bool
    lark_cli_path: str | None
    feishu_chat_id: str | None
    feishu_user_id: str | None
    windows_notifications_enabled: bool

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = Path(os.getenv("LINUXDO_DATA_DIR", "data")).resolve()
        browser_dir = data_dir / "browser"
        return cls(
            base_url=os.getenv("LINUXDO_BASE_URL", "https://linux.do").rstrip("/"),
            data_dir=data_dir,
            database_path=(data_dir / "linuxdo.sqlite3").resolve(),
            browser_profile_dir=browser_dir / "profile",
            browser_debug_profile_dir=browser_dir / "cdp-user-data",
            storage_state_path=browser_dir / "storage_state.json",
            session_meta_path=browser_dir / "session_meta.json",
            browser_executable=detect_browser_executable(),
            browser_cookie_source=os.getenv("LINUXDO_BROWSER_COOKIE_SOURCE", "auto"),
            browser_profile_name=os.getenv("LINUXDO_BROWSER_PROFILE"),
            browser_cdp_url=os.getenv("LINUXDO_BROWSER_CDP_URL"),
            require_login=env_bool("LINUXDO_REQUIRE_LOGIN", False),
            bridge_host=os.getenv("LINUXDO_BRIDGE_HOST", "127.0.0.1"),
            bridge_port=int(os.getenv("LINUXDO_BRIDGE_PORT", "8765")),
            bridge_token=os.getenv("LINUXDO_BRIDGE_TOKEN"),
            poll_interval_seconds=int(os.getenv("LINUXDO_POLL_INTERVAL_SECONDS", "300")),
            bootstrap_limit=int(os.getenv("LINUXDO_BOOTSTRAP_LIMIT", "30")),
            max_pages_per_run=int(os.getenv("LINUXDO_MAX_PAGES_PER_RUN", "10")),
            browser_fallback_headless=env_bool("LINUXDO_BROWSER_FALLBACK_HEADLESS", True),
            auth_wait_timeout_seconds=int(os.getenv("LINUXDO_AUTH_WAIT_TIMEOUT_SECONDS", "900")),
            llm_batch_size=max(1, int(os.getenv("LINUXDO_LLM_BATCH_SIZE", "10"))),
            llm_retry_limit=max(0, int(os.getenv("LINUXDO_LLM_RETRY_LIMIT", "3"))),
            openai_api_key=os.getenv("OPENAI_API_KEY"),
            openai_base_url=os.getenv("OPENAI_BASE_URL"),
            openai_model=os.getenv("OPENAI_MODEL", "openai/gpt-oss-120b"),
            smtp_host=os.getenv("SMTP_HOST"),
            smtp_port=int(os.getenv("SMTP_PORT", "465")),
            smtp_username=os.getenv("SMTP_USERNAME"),
            smtp_password=os.getenv("SMTP_PASSWORD"),
            smtp_sender=os.getenv("SMTP_SENDER"),
            smtp_recipient=os.getenv("SMTP_RECIPIENT"),
            smtp_use_tls=env_bool("SMTP_USE_TLS", False),
            lark_cli_path=detect_lark_cli_executable(),
            feishu_chat_id=os.getenv("FEISHU_CHAT_ID"),
            feishu_user_id=os.getenv("FEISHU_USER_ID"),
            windows_notifications_enabled=env_bool(
                "LINUXDO_WINDOWS_NOTIFICATIONS",
                os.name == "nt",
            ),
        )

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.browser_profile_dir.mkdir(parents=True, exist_ok=True)
        self.browser_debug_profile_dir.mkdir(parents=True, exist_ok=True)
        self.storage_state_path.parent.mkdir(parents=True, exist_ok=True)
