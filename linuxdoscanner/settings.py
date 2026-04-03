from __future__ import annotations

import os
import shutil
import sys
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .runtime_paths import app_root

PROJECT_ROOT = app_root()
DEFAULT_CONFIG_PATH = Path("config/settings.toml")
DEFAULT_OUTPUT_DIR = Path("output")
DEFAULT_STATE_DIR = DEFAULT_OUTPUT_DIR
DEFAULT_DATABASE_DIR = DEFAULT_STATE_DIR / "databases"
DEFAULT_BROWSER_DIR = DEFAULT_STATE_DIR / "browser"
DEFAULT_REPORTS_DIR = DEFAULT_OUTPUT_DIR / "reports"
DEFAULT_EVAL_REPORT_PATH = DEFAULT_REPORTS_DIR / "model_eval_logged_batches_deepseek_minimax.json"


def env_bool(name: str, default: bool = False) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def detect_browser_executable(configured_path: str | None = None) -> str | None:
    candidates = [
        configured_path,
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


def detect_lark_cli_executable(configured_path: str | None = None) -> str | None:
    candidates = [
        configured_path,
        os.getenv("LARK_CLI_PATH"),
        r"C:\Program Files\nodejs\lark-cli.cmd",
        r"C:\Program Files\nodejs\lark-cli",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def _resolve_path(project_root: Path, raw_path: str | None, default: Path) -> Path:
    candidate = Path(raw_path) if raw_path else default
    if not candidate.is_absolute():
        candidate = project_root / candidate
    return candidate.resolve()


def _read_config(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        return {}
    with config_path.open("rb") as handle:
        payload = tomllib.load(handle)
    return payload if isinstance(payload, dict) else {}


def _config_value(config: dict[str, Any], *keys: str, default: Any = None) -> Any:
    current: Any = config
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _string_setting(config: dict[str, Any], env_name: str, keys: tuple[str, ...], default: str) -> str:
    env_value = os.getenv(env_name)
    if env_value is not None:
        return env_value.strip() or default
    config_value = _config_value(config, *keys)
    if config_value is None:
        return default
    text = str(config_value).strip()
    return text or default


def _optional_string_setting(config: dict[str, Any], env_name: str, keys: tuple[str, ...]) -> str | None:
    env_value = os.getenv(env_name)
    if env_value is not None:
        return _optional_str(env_value)
    return _optional_str(_config_value(config, *keys))


def _int_setting(config: dict[str, Any], env_name: str, keys: tuple[str, ...], default: int) -> int:
    env_value = os.getenv(env_name)
    if env_value is not None:
        return int(env_value)
    config_value = _config_value(config, *keys, default=default)
    return int(config_value)


def _bool_setting(config: dict[str, Any], env_name: str, keys: tuple[str, ...], default: bool) -> bool:
    if os.getenv(env_name) is not None:
        return env_bool(env_name, default)
    config_value = _config_value(config, *keys, default=default)
    if isinstance(config_value, bool):
        return config_value
    if config_value is None:
        return default
    return str(config_value).strip().lower() in {"1", "true", "yes", "on"}


def _int_setting_with_fallback(
    config: dict[str, Any],
    env_name: str,
    keys: tuple[str, ...],
    *,
    fallback_keys: tuple[str, ...] | None = None,
    default: int,
) -> int:
    env_value = os.getenv(env_name)
    if env_value is not None:
        return int(env_value)
    fallback_value = _config_value(config, *fallback_keys, default=default) if fallback_keys else default
    config_value = _config_value(config, *keys, default=fallback_value)
    return int(config_value)


def _normalize_delay_range(minimum: int, maximum: int) -> tuple[int, int]:
    normalized_minimum = max(0, int(minimum))
    normalized_maximum = max(0, int(maximum))
    if normalized_maximum < normalized_minimum:
        normalized_minimum, normalized_maximum = normalized_maximum, normalized_minimum
    return normalized_minimum, normalized_maximum


def _path_has_contents(path: Path) -> bool:
    return path.exists() and any(path.iterdir())


def _same_path(first: Path, second: Path) -> bool:
    return first.resolve(strict=False) == second.resolve(strict=False)


def _copy_missing_tree(source: Path, destination: Path) -> None:
    if not source.exists() or _same_path(source, destination):
        return

    if source.is_file():
        if destination.exists():
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return

    destination.mkdir(parents=True, exist_ok=True)
    for child in source.iterdir():
        _copy_missing_tree(child, destination / child.name)


@dataclass(slots=True)
class Settings:
    project_root: Path
    config_path: Path
    output_dir: Path
    logs_root_dir: Path
    reports_dir: Path
    state_dir: Path
    database_dir: Path
    database_path: Path
    browser_root_dir: Path
    browser_profile_dir: Path
    browser_debug_profile_dir: Path
    storage_state_path: Path
    session_meta_path: Path
    base_url: str
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
    page_request_delay_min_seconds: int
    page_request_delay_max_seconds: int
    round_delay_min_seconds: int
    round_delay_max_seconds: int
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
    def from_env(cls, project_root: Path | None = None) -> "Settings":
        resolved_project_root = (project_root or PROJECT_ROOT).resolve()
        config_path_raw = os.getenv("LINUXDO_CONFIG_FILE")
        config_path = _resolve_path(resolved_project_root, config_path_raw, DEFAULT_CONFIG_PATH)
        config = _read_config(config_path)

        output_dir = _resolve_path(
            resolved_project_root,
            _optional_string_setting(config, "LINUXDO_OUTPUT_DIR", ("paths", "output_dir")),
            DEFAULT_OUTPUT_DIR,
        )
        default_state_dir = output_dir
        state_dir = _resolve_path(
            resolved_project_root,
            _optional_string_setting(config, "LINUXDO_STATE_DIR", ("paths", "state_dir")),
            default_state_dir,
        )
        default_database_dir = state_dir / DEFAULT_DATABASE_DIR.name
        database_dir = _resolve_path(
            resolved_project_root,
            _optional_string_setting(config, "LINUXDO_DATABASE_DIR", ("paths", "database_dir")),
            default_database_dir,
        )
        default_browser_dir = state_dir / DEFAULT_BROWSER_DIR.name
        browser_root_dir = _resolve_path(
            resolved_project_root,
            _optional_string_setting(config, "LINUXDO_BROWSER_DIR", ("paths", "browser_dir")),
            default_browser_dir,
        )
        browser_executable = detect_browser_executable(
            _optional_string_setting(config, "LINUXDO_BROWSER_EXECUTABLE", ("browser", "executable"))
        )
        page_request_delay_min_seconds, page_request_delay_max_seconds = _normalize_delay_range(
            _int_setting_with_fallback(
                config,
                "LINUXDO_PAGE_REQUEST_DELAY_MIN_SECONDS",
                ("crawl", "page_request_delay_min_seconds"),
                default=1,
            ),
            _int_setting_with_fallback(
                config,
                "LINUXDO_PAGE_REQUEST_DELAY_MAX_SECONDS",
                ("crawl", "page_request_delay_max_seconds"),
                default=10,
            ),
        )
        round_delay_min_seconds, round_delay_max_seconds = _normalize_delay_range(
            _int_setting_with_fallback(
                config,
                "LINUXDO_ROUND_DELAY_MIN_SECONDS",
                ("crawl", "round_delay_min_seconds"),
                default=1,
            ),
            _int_setting_with_fallback(
                config,
                "LINUXDO_ROUND_DELAY_MAX_SECONDS",
                ("crawl", "round_delay_max_seconds"),
                default=180,
            ),
        )

        return cls(
            project_root=resolved_project_root,
            config_path=config_path,
            output_dir=output_dir,
            logs_root_dir=output_dir / "logs",
            reports_dir=output_dir / "reports",
            state_dir=state_dir,
            database_dir=database_dir,
            database_path=(database_dir / "linuxdo.sqlite3").resolve(),
            browser_root_dir=browser_root_dir,
            browser_profile_dir=(browser_root_dir / "profile").resolve(),
            browser_debug_profile_dir=(browser_root_dir / "cdp-user-data").resolve(),
            storage_state_path=(browser_root_dir / "storage_state.json").resolve(),
            session_meta_path=(browser_root_dir / "session_meta.json").resolve(),
            base_url=_string_setting(config, "LINUXDO_BASE_URL", ("app", "base_url"), "https://linux.do").rstrip("/"),
            browser_executable=browser_executable,
            browser_cookie_source=_string_setting(config, "LINUXDO_BROWSER_COOKIE_SOURCE", ("browser", "cookie_source"), "auto"),
            browser_profile_name=_optional_string_setting(config, "LINUXDO_BROWSER_PROFILE", ("browser", "profile_name")),
            browser_cdp_url=_optional_string_setting(config, "LINUXDO_BROWSER_CDP_URL", ("browser", "cdp_url")),
            require_login=_bool_setting(config, "LINUXDO_REQUIRE_LOGIN", ("app", "require_login"), False),
            bridge_host=_string_setting(config, "LINUXDO_BRIDGE_HOST", ("app", "bridge_host"), "127.0.0.1"),
            bridge_port=_int_setting(config, "LINUXDO_BRIDGE_PORT", ("app", "bridge_port"), 8765),
            bridge_token=_optional_string_setting(config, "LINUXDO_BRIDGE_TOKEN", ("app", "bridge_token")),
            poll_interval_seconds=_int_setting(config, "LINUXDO_POLL_INTERVAL_SECONDS", ("app", "poll_interval_seconds"), 300),
            bootstrap_limit=_int_setting(config, "LINUXDO_BOOTSTRAP_LIMIT", ("app", "bootstrap_limit"), 30),
            max_pages_per_run=_int_setting_with_fallback(
                config,
                "LINUXDO_MAX_PAGES_PER_RUN",
                ("crawl", "max_pages_per_run"),
                fallback_keys=("app", "max_pages_per_run"),
                default=10,
            ),
            page_request_delay_min_seconds=page_request_delay_min_seconds,
            page_request_delay_max_seconds=page_request_delay_max_seconds,
            round_delay_min_seconds=round_delay_min_seconds,
            round_delay_max_seconds=round_delay_max_seconds,
            browser_fallback_headless=_bool_setting(
                config,
                "LINUXDO_BROWSER_FALLBACK_HEADLESS",
                ("browser", "fallback_headless"),
                True,
            ),
            auth_wait_timeout_seconds=_int_setting(
                config,
                "LINUXDO_AUTH_WAIT_TIMEOUT_SECONDS",
                ("app", "auth_wait_timeout_seconds"),
                900,
            ),
            llm_batch_size=max(1, _int_setting(config, "LINUXDO_LLM_BATCH_SIZE", ("llm", "batch_size"), 10)),
            llm_retry_limit=max(0, _int_setting(config, "LINUXDO_LLM_RETRY_LIMIT", ("llm", "retry_limit"), 3)),
            openai_api_key=_optional_string_setting(config, "OPENAI_API_KEY", ("llm", "api_key")),
            openai_base_url=_optional_string_setting(config, "OPENAI_BASE_URL", ("llm", "base_url")),
            openai_model=_optional_string_setting(config, "OPENAI_MODEL", ("llm", "model")) or "openai/gpt-oss-120b",
            smtp_host=_optional_string_setting(config, "SMTP_HOST", ("email", "host")),
            smtp_port=_int_setting(config, "SMTP_PORT", ("email", "port"), 465),
            smtp_username=_optional_string_setting(config, "SMTP_USERNAME", ("email", "username")),
            smtp_password=_optional_string_setting(config, "SMTP_PASSWORD", ("email", "password")),
            smtp_sender=_optional_string_setting(config, "SMTP_SENDER", ("email", "sender")),
            smtp_recipient=_optional_string_setting(config, "SMTP_RECIPIENT", ("email", "recipient")),
            smtp_use_tls=_bool_setting(config, "SMTP_USE_TLS", ("email", "use_tls"), False),
            lark_cli_path=detect_lark_cli_executable(
                _optional_string_setting(config, "LARK_CLI_PATH", ("feishu", "lark_cli_path"))
            ),
            feishu_chat_id=_optional_string_setting(config, "FEISHU_CHAT_ID", ("feishu", "chat_id")),
            feishu_user_id=_optional_string_setting(config, "FEISHU_USER_ID", ("feishu", "user_id")),
            windows_notifications_enabled=_bool_setting(
                config,
                "LINUXDO_WINDOWS_NOTIFICATIONS",
                ("app", "windows_notifications_enabled"),
                sys.platform.startswith("win"),
            ),
        )

    def ensure_directories(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.logs_root_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.database_dir.mkdir(parents=True, exist_ok=True)
        self.browser_root_dir.mkdir(parents=True, exist_ok=True)
        self.browser_profile_dir.mkdir(parents=True, exist_ok=True)
        self.browser_debug_profile_dir.mkdir(parents=True, exist_ok=True)
        self.storage_state_path.parent.mkdir(parents=True, exist_ok=True)
        self._bootstrap_legacy_data()

    def log_file_paths(self, now: datetime | None = None) -> tuple[Path, Path]:
        current = now or datetime.now()
        day_dir = self.logs_root_dir / current.strftime("%Y") / current.strftime("%m") / current.strftime("%d")
        day_name = current.strftime("%d")
        return (day_dir / f"{day_name}_info.log").resolve(), (day_dir / f"{day_name}_error.log").resolve()

    def _bootstrap_legacy_data(self) -> None:
        legacy_hidden_output_dir = self.project_root / ".output"
        if legacy_hidden_output_dir.exists():
            _copy_missing_tree(legacy_hidden_output_dir, self.output_dir)

        legacy_data_dir = self.project_root / "data"
        legacy_database_path = legacy_data_dir / "linuxdo.sqlite3"
        if not self.database_path.exists() and legacy_database_path.exists():
            shutil.copy2(legacy_database_path, self.database_path)

        legacy_browser_root = legacy_data_dir / "browser"
        has_browser_state = (
            self.storage_state_path.exists()
            or _path_has_contents(self.browser_profile_dir)
            or _path_has_contents(self.browser_debug_profile_dir)
        )
        if not has_browser_state and legacy_browser_root.exists():
            shutil.copytree(legacy_browser_root, self.browser_root_dir, dirs_exist_ok=True)
