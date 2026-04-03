from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

from .settings import Settings
from .storage import Database, utc_now


SUPPORTED_PROVIDER_TYPES = {"openai_compatible", "newapi"}
DEFAULT_FOCUS_KEYWORDS = [
    "AI前沿",
    "模型更新",
    "实验复现",
    "辟谣实测",
    "Codex技巧",
    "ClaudeCode技巧",
    "羊毛福利",
    "公益站",
]
DEFAULT_FOCUS_PROMPT = (
    "优先识别高密度、可执行、对 AI 使用和判断有直接价值的主题。"
    "当前重点关注 AI 前沿新闻与模型更新、实验复现、辟谣实测、Codex 或 Claude Code 使用技巧，"
    "以及标题、摘要或正文明确给出具体价值点的福利、公益站、资源信息。"
    "对 Linux.do 的福利帖，不必把完整领取步骤当成唯一条件；只要标题或摘要已明确写出可领福利、折扣、额度、入口、平台等信息，且正文不矛盾，就可以视为有效信号。"
    "正文证据仍优先于标题；注册成功庆祝、闲聊、女装整活、树洞、感情和生活水贴不要误判。"
)
LEGACY_DEFAULT_FOCUS_PROMPTS = (
    "优先识别高密度、可执行、对 AI 使用和判断有直接价值的主题。"
    "当前重点关注 AI 前沿新闻与模型更新、实验复现、辟谣实测、Codex 或 Claude Code 使用技巧，"
    "以及有明确路径或价值点的福利、公益站、资源信息。"
    "正文证据必须优先于标题；注册成功庆祝、闲聊、女装整活、树洞、感情和生活水贴不要误判。",
)
DEFAULT_NOTIFICATION_PROMPT = (
    "只有当主题具备较强时效性、信息密度和行动价值时，才标记为需要通知。"
    "AI 相关新闻、严谨实验或辟谣实测、Codex/Claude Code 使用技巧，应明显倾向通知；"
    "福利或公益站资源贴，只要标题、摘要或正文明确表明存在可领取福利、折扣、额度、入口、平台、CDK/邀请码、模型范围、开放规则等具体价值点，且不是单纯求助、猜测或空泛转述，即使没有完整领取步骤，也应明显倾向通知；"
    "Linux.do 上标题明确写福利的帖子通常不是标题党，可把这类标题视为强信号，但若正文明确否定或与标题矛盾，仍以正文为准；"
    "注册庆祝、闲聊、女装整活、感情贴、树洞和纯吐槽不要通知。"
    "最终是否推送只看 requires_notification 这一个字段，拿不准时宁可标 false。"
)
LEGACY_DEFAULT_NOTIFICATION_PROMPTS = (
    "只有当主题具备较强时效性、信息密度和行动价值时，才标记为需要通知。"
    "AI 相关新闻、严谨实验或辟谣实测、Codex/Claude Code 使用技巧，应明显倾向通知；"
    "正文直接给出福利路径、公益站入口、API 地址、密钥、额度、模型范围或具体使用方式的资源贴，也应明显倾向通知；"
    "注册庆祝、闲聊、女装整活、感情贴、树洞和纯吐槽不要通知。"
    "最终是否推送只看 requires_notification 这一个字段，拿不准时宁可标 false。",
)
_MISSING = object()


@dataclass(slots=True)
class AIProviderConfig:
    provider_type: str = "openai_compatible"
    base_url: str = ""
    api_key: str = ""
    selected_model: str = ""
    available_models: list[dict[str, str | None]] = field(default_factory=list)
    last_model_sync_at: str | None = None
    last_model_sync_error: str | None = None
    focus_keywords: list[str] = field(default_factory=lambda: list(DEFAULT_FOCUS_KEYWORDS))
    focus_prompt: str = DEFAULT_FOCUS_PROMPT
    notification_prompt: str = DEFAULT_NOTIFICATION_PROMPT

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "AIProviderConfig":
        payload = payload or {}
        provider_type = str(payload.get("provider_type") or "openai_compatible").strip()
        if provider_type not in SUPPORTED_PROVIDER_TYPES:
            provider_type = "openai_compatible"
        return cls(
            provider_type=provider_type,
            base_url=str(payload.get("base_url") or "").strip(),
            api_key=str(payload.get("api_key") or "").strip(),
            selected_model=str(payload.get("selected_model") or "").strip(),
            available_models=_normalize_model_list(payload.get("available_models") or []),
            last_model_sync_at=_optional_str(payload.get("last_model_sync_at")),
            last_model_sync_error=_optional_str(payload.get("last_model_sync_error")),
            focus_keywords=_normalize_text_list(
                payload.get("focus_keywords", _MISSING),
                default=DEFAULT_FOCUS_KEYWORDS,
            ),
            focus_prompt=_normalize_prompt_text(
                payload.get("focus_prompt", _MISSING),
                default=DEFAULT_FOCUS_PROMPT,
                legacy_defaults=LEGACY_DEFAULT_FOCUS_PROMPTS,
            ),
            notification_prompt=_normalize_prompt_text(
                payload.get("notification_prompt", _MISSING),
                default=DEFAULT_NOTIFICATION_PROMPT,
                legacy_defaults=LEGACY_DEFAULT_NOTIFICATION_PROMPTS,
            ),
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> "AIProviderConfig":
        return cls(
            provider_type="openai_compatible",
            base_url=str(settings.openai_base_url or "").strip(),
            api_key=str(settings.openai_api_key or "").strip(),
            selected_model=str(settings.openai_model or "").strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_type": self.provider_type,
            "base_url": self.base_url,
            "api_key": self.api_key,
            "selected_model": self.selected_model,
            "available_models": self.available_models,
            "last_model_sync_at": self.last_model_sync_at,
            "last_model_sync_error": self.last_model_sync_error,
            "focus_keywords": self.focus_keywords,
            "focus_prompt": self.focus_prompt,
            "notification_prompt": self.notification_prompt,
        }

    def sanitized(self) -> "AIProviderConfig":
        provider_type = self.provider_type if self.provider_type in SUPPORTED_PROVIDER_TYPES else "openai_compatible"
        return AIProviderConfig(
            provider_type=provider_type,
            base_url=str(self.base_url or "").strip(),
            api_key=str(self.api_key or "").strip(),
            selected_model=str(self.selected_model or "").strip(),
            available_models=_normalize_model_list(self.available_models),
            last_model_sync_error=_optional_str(self.last_model_sync_error),
            last_model_sync_at=_optional_str(self.last_model_sync_at),
            focus_keywords=_normalize_text_list(self.focus_keywords),
            focus_prompt=_normalize_prompt_text(
                self.focus_prompt,
                default=DEFAULT_FOCUS_PROMPT,
                legacy_defaults=LEGACY_DEFAULT_FOCUS_PROMPTS,
            ),
            notification_prompt=_normalize_prompt_text(
                self.notification_prompt,
                default=DEFAULT_NOTIFICATION_PROMPT,
                legacy_defaults=LEGACY_DEFAULT_NOTIFICATION_PROMPTS,
            ),
        )

    @property
    def is_llm_enabled(self) -> bool:
        return bool(self.api_key and self.selected_model and self.base_url)


class AIConfigManager:
    def __init__(self, settings: Settings, database: Database):
        self.settings = settings
        self.database = database

    def load_config(self, use_fallback: bool = True) -> AIProviderConfig:
        payload = self.database.get_app_config_json("ai_config")
        if isinstance(payload, dict):
            return AIProviderConfig.from_dict(payload)
        if use_fallback:
            return AIProviderConfig.from_settings(self.settings)
        return AIProviderConfig()

    def save_config(self, payload: dict[str, Any] | AIProviderConfig) -> AIProviderConfig:
        current = self.load_config(use_fallback=False)
        if isinstance(payload, AIProviderConfig):
            merged_payload = payload.to_dict()
        else:
            merged_payload = current.to_dict()
            merged_payload.update(payload)
        merged = AIProviderConfig.from_dict(merged_payload).sanitized()
        self.database.set_app_config_json("ai_config", merged.to_dict())
        return merged

    def sync_models(self, payload: dict[str, Any] | None = None) -> AIProviderConfig:
        config = self.load_config()
        if payload:
            config = self.save_config(payload)

        if not config.base_url:
            message = "请先填写 AI Base URL。"
            self._save_sync_error(config, message)
            raise ValueError(message)
        if not config.api_key:
            message = "请先填写 AI API Key。"
            self._save_sync_error(config, message)
            raise ValueError(message)

        models_url = normalize_models_url(config.base_url)
        try:
            with httpx.Client(timeout=20.0, follow_redirects=True) as client:
                response = client.get(
                    models_url,
                    headers={
                        "Accept": "application/json",
                        "Authorization": f"Bearer {config.api_key}",
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            message = f"模型同步失败: {exc}"
            self._save_sync_error(config, message)
            raise RuntimeError(message) from exc

        models = _normalize_model_list((payload or {}).get("data") or [])
        synced = AIProviderConfig.from_dict(
            {
                **config.to_dict(),
                "available_models": models,
                "last_model_sync_at": utc_now(),
                "last_model_sync_error": None,
            }
        ).sanitized()
        self.database.set_app_config_json("ai_config", synced.to_dict())
        return synced

    def _save_sync_error(self, config: AIProviderConfig, message: str) -> None:
        failed = AIProviderConfig.from_dict(
            {
                **config.to_dict(),
                "last_model_sync_error": message,
            }
        ).sanitized()
        self.database.set_app_config_json("ai_config", failed.to_dict())


def normalize_chat_base_url(base_url: str | None) -> str | None:
    raw = str(base_url or "").strip()
    if not raw:
        return None

    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("AI Base URL 必须是完整的 http/https 地址。")

    path = _strip_endpoint_suffix(parsed.path.rstrip("/"))
    if not path:
        path = "/v1"
    elif not path.endswith("/v1"):
        path = f"{path}/v1"

    normalized = parsed._replace(path=path, params="", query="", fragment="")
    return urlunparse(normalized)


def normalize_models_url(base_url: str | None) -> str:
    chat_base_url = normalize_chat_base_url(base_url)
    if chat_base_url is None:
        raise ValueError("AI Base URL 不能为空。")
    return f"{chat_base_url.rstrip('/')}/models"


def _strip_endpoint_suffix(path: str) -> str:
    suffixes = [
        "/models",
        "/chat/completions",
        "/completions",
        "/embeddings",
        "/responses",
    ]
    for suffix in suffixes:
        if path.endswith(suffix):
            return path[: -len(suffix)]
    return path


def _normalize_model_list(raw_models: list[Any]) -> list[dict[str, str | None]]:
    deduped: dict[str, dict[str, str | None]] = {}
    for item in raw_models:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or "").strip()
        if not model_id:
            continue
        owned_by = _optional_str(item.get("owned_by"))
        deduped[model_id] = {"id": model_id, "owned_by": owned_by}
    return [deduped[key] for key in sorted(deduped, key=lambda value: value.lower())]


def _normalize_text_list(value: Any, default: list[str] | tuple[str, ...] | object = _MISSING) -> list[str]:
    if value is _MISSING:
        return [str(item).strip() for item in default] if isinstance(default, (list, tuple)) else []

    if isinstance(value, str):
        parts = [part.strip() for part in value.replace("，", ",").replace("\n", ",").split(",")]
    elif isinstance(value, list):
        parts = [str(item).strip() for item in value]
    else:
        parts = [str(value).strip()] if value is not None else []

    normalized: list[str] = []
    seen: set[str] = set()
    for part in parts:
        if not part or part in seen:
            continue
        seen.add(part)
        normalized.append(part[:80])
    return normalized


def _normalize_text(value: Any, default: str | object = _MISSING) -> str:
    if value is _MISSING:
        return str(default) if isinstance(default, str) else ""
    if value is None:
        return ""
    return str(value).strip()


def _normalize_prompt_text(
    value: Any,
    *,
    default: str,
    legacy_defaults: tuple[str, ...] = (),
) -> str:
    normalized = _normalize_text(value, default=default)
    if normalized in legacy_defaults:
        return default
    return normalized


def _optional_str(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None
