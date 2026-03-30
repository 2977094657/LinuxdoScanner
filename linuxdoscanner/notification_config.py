from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .settings import Settings
from .storage import Database


@dataclass(slots=True)
class NotificationConfig:
    feishu_enabled: bool = False
    lark_cli_path: str = ""
    feishu_chat_id: str = ""
    feishu_user_id: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "NotificationConfig":
        payload = payload or {}
        return cls(
            feishu_enabled=bool(payload.get("feishu_enabled")),
            lark_cli_path=str(payload.get("lark_cli_path") or "").strip(),
            feishu_chat_id=str(payload.get("feishu_chat_id") or "").strip(),
            feishu_user_id=str(payload.get("feishu_user_id") or "").strip(),
        ).sanitized()

    @classmethod
    def from_settings(cls, settings: Settings) -> "NotificationConfig":
        return cls(
            feishu_enabled=bool(settings.feishu_chat_id or settings.feishu_user_id),
            lark_cli_path=str(settings.lark_cli_path or "").strip(),
            feishu_chat_id=str(settings.feishu_chat_id or "").strip(),
            feishu_user_id=str(settings.feishu_user_id or "").strip(),
        ).sanitized()

    def to_dict(self) -> dict[str, Any]:
        return {
            "feishu_enabled": self.feishu_enabled,
            "lark_cli_path": self.lark_cli_path,
            "feishu_chat_id": self.feishu_chat_id,
            "feishu_user_id": self.feishu_user_id,
        }

    def sanitized(self) -> "NotificationConfig":
        return NotificationConfig(
            feishu_enabled=bool(self.feishu_enabled),
            lark_cli_path=str(self.lark_cli_path or "").strip(),
            feishu_chat_id=str(self.feishu_chat_id or "").strip(),
            feishu_user_id=str(self.feishu_user_id or "").strip(),
        )

    @property
    def destination_type(self) -> str:
        if self.feishu_chat_id:
            return "chat"
        if self.feishu_user_id:
            return "user"
        return "none"

    @property
    def is_feishu_configured(self) -> bool:
        return bool(self.feishu_enabled and self.lark_cli_path and (self.feishu_chat_id or self.feishu_user_id))


class NotificationConfigManager:
    def __init__(self, settings: Settings, database: Database):
        self.settings = settings
        self.database = database

    def load_config(self, use_fallback: bool = True) -> NotificationConfig:
        payload = self.database.get_app_config_json("notification_config")
        if isinstance(payload, dict):
            return NotificationConfig.from_dict(payload)
        if use_fallback:
            return NotificationConfig.from_settings(self.settings)
        return NotificationConfig()

    def save_config(self, payload: dict[str, Any] | NotificationConfig) -> NotificationConfig:
        if isinstance(payload, NotificationConfig):
            config = payload.sanitized()
        else:
            current = self.load_config(use_fallback=False)
            merged = current.to_dict()
            merged.update(payload)
            config = NotificationConfig.from_dict(merged)
        self.database.set_app_config_json("notification_config", config.to_dict())
        return config
