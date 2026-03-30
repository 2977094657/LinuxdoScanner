from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from .ai_config import AIProviderConfig, normalize_chat_base_url
from .models import TopicAnalysis, TopicPayload, normalize_topic_tags
from .settings import Settings


LOGGER = logging.getLogger(__name__)

LLM_REQUEST_TIMEOUT_SECONDS = 180
LLM_USER_AGENT = "LinuxdoScanner/1.0"
LLM_MAX_TITLE_CHARS = 200
LLM_MAX_CONTENT_CHARS = 1800
DEFAULT_TOPIC_LABEL = "普通讨论"
PREFERRED_TOPIC_TAGS = [
    "AI相关",
    "AI前沿",
    "前沿快讯",
    "模型更新",
    "实验复现",
    "辟谣实测",
    "严谨评测",
    "Codex技巧",
    "ClaudeCode技巧",
    "AI工作流",
    "功能发布",
    "工具发布",
    "教程攻略",
    "订阅计费",
    "注册风控",
    "号池养号",
    "故障状态",
    "公益站",
    "羊毛福利",
    "资源分享",
    "求助问答",
    "开发调优",
    "产品测评",
    "注册庆祝",
    "闲聊水帖",
    "感情树洞",
    "生活求助",
    "女装整活",
    "站务等级",
]
BLOCKED_NOTIFY_LABELS = {
    "注册庆祝",
    "闲聊",
    "闲聊水帖",
    "树洞",
    "感情话题",
    "感情树洞",
    "生活求助",
    "女装整活",
    "站务等级",
}
LABEL_ALIASES = {
    "general": DEFAULT_TOPIC_LABEL,
    "frontier_news": "前沿快讯",
    "deal": "羊毛福利",
    "public_benefit_site": "公益站",
    "ai_reflection": "AI反思",
    "claude code技巧": "ClaudeCode技巧",
    "claudecode技巧": "ClaudeCode技巧",
    "cc技巧": "ClaudeCode技巧",
    "站点福利": "公益站",
    "福利": "羊毛福利",
    "羊毛": "羊毛福利",
    "闲聊": "闲聊水帖",
    "树洞": "感情树洞",
    "感情话题": "感情树洞",
    "升级庆祝": "站务等级",
    "等级升级": "站务等级",
}
LLM_SECRET_PATTERNS = (
    re.compile(r"(?i)(api\s*key[:：]\s*)([^\s,，。；;]+)"),
    re.compile(r"\bsk-[A-Za-z0-9._-]{8,}\b"),
    re.compile(r"\b[A-Za-z0-9_-]{24,}\b"),
    re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", flags=re.IGNORECASE),
)


class LLMRequestError(RuntimeError):
    def __init__(self, *, status_code: int, message: str, body: Any):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class TopicClassifier:
    def __init__(self, settings: Settings, ai_config: AIProviderConfig | None = None):
        self.settings = settings
        self.ai_config = ai_config or AIProviderConfig.from_settings(settings)
        self.model_name = self.ai_config.selected_model
        self._llm_base_url = normalize_chat_base_url(self.ai_config.base_url or settings.openai_base_url)
        self._llm_chat_url = f"{self._llm_base_url.rstrip('/')}/chat/completions" if self._llm_base_url else None
        self._llm_http: httpx.Client | None = None

        if self.ai_config.api_key and self.model_name and self._llm_chat_url:
            self._llm_http = httpx.Client(
                timeout=LLM_REQUEST_TIMEOUT_SECONDS,
                follow_redirects=True,
                headers={
                    "Authorization": f"Bearer {self.ai_config.api_key}",
                    "User-Agent": LLM_USER_AGENT,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )

    def analyze(self, payload: TopicPayload) -> TopicAnalysis:
        return self.analyze_many([payload])[0]

    def analyze_many(self, payloads: list[TopicPayload]) -> list[TopicAnalysis]:
        if not payloads:
            return []

        if self._llm_http is None or not self.model_name or not self._llm_chat_url:
            LOGGER.warning("AI classification unavailable; storing topics without AI labels.")
            return [
                self._neutral_analysis(
                    summary="AI 未配置或当前不可用，未执行识别。",
                    reason="当前仅允许 AI 识别，未使用规则兜底。",
                    provider="llm_unavailable",
                )
                for _ in payloads
            ]

        results: list[TopicAnalysis] = []
        for batch in self._batched(payloads, self.settings.llm_batch_size):
            try:
                results.extend(self._llm_analyze_batch_adaptive(batch))
            except Exception as exc:  # pragma: no cover - remote I/O
                topic_ids = [payload.topic_id for payload in batch]
                LOGGER.warning("LLM batch enhancement failed for topics %s: %s", topic_ids, exc)
                LOGGER.warning("LLM batch enhancement exception details: %s", self._serialize_llm_exception(exc))
                failed = self._neutral_analysis(
                    summary="AI 识别失败，当前批次未生成标签。",
                    reason="AI 接口请求失败，未使用规则兜底。",
                    provider="llm_failed",
                )
                results.extend(failed for _ in batch)
        return results

    def _neutral_analysis(
        self,
        *,
        summary: str = "等待 AI 识别。",
        reason: str = "当前仅允许 AI 识别，未使用规则兜底。",
        provider: str = "llm_pending",
    ) -> TopicAnalysis:
        return TopicAnalysis(
            primary_label="AI识别失败",
            labels=["AI识别失败"],
            summary=summary,
            reasons=[reason],
            provider=provider,
            requires_notification=False,
        )

    def _llm_analyze_batch_adaptive(self, payloads: list[TopicPayload]) -> list[TopicAnalysis]:
        try:
            return self._llm_analyze_batch(payloads)
        except Exception as exc:
            if not self._should_retry_with_smaller_batch(exc, payloads):
                raise
            split_index = max(1, len(payloads) // 2)
            left = payloads[:split_index]
            right = payloads[split_index:]
            LOGGER.warning(
                "LLM batch request hit a retryable upstream failure; retrying with smaller batches %s + %s for topics %s",
                len(left),
                len(right),
                [payload.topic_id for payload in payloads],
            )
            results = self._llm_analyze_batch_adaptive(left)
            if right:
                results.extend(self._llm_analyze_batch_adaptive(right))
            return results

    def _llm_analyze_batch(self, payloads: list[TopicPayload]) -> list[TopicAnalysis]:
        if self._llm_http is None or not self.model_name:  # pragma: no cover - guarded by caller
            return [self._neutral_analysis() for _ in payloads]

        documents = [self._build_prompt_payload(payload) for payload in payloads]
        request_payload = {
            "model": self.model_name,
            "temperature": 0,
            "messages": [
                {
                    "role": "user",
                    "content": self._build_user_content(
                        payload={
                            "focus_keywords": self.ai_config.focus_keywords,
                            "focus_prompt": self.ai_config.focus_prompt,
                            "notification_prompt": self.ai_config.notification_prompt,
                            "batch_size": len(documents),
                            "documents": documents,
                        }
                    ),
                },
            ],
        }
        self._log_llm_request(
            kind="batch",
            topic_ids=[payload.topic_id for payload in payloads],
            request_payload=request_payload,
        )
        content = self._request_llm_content(request_payload)
        self._log_llm_response(
            kind="batch",
            topic_ids=[payload.topic_id for payload in payloads],
            response_content=content,
        )

        parsed = self._parse_llm_json_value(content)
        raw_results: list[Any]
        if isinstance(parsed, list):
            raw_results = parsed
        elif isinstance(parsed, dict) and isinstance(parsed.get("results"), list):
            raw_results = parsed["results"]
        else:
            raise ValueError("LLM response did not contain a top-level JSON array.")

        results_by_topic_id: dict[int, TopicAnalysis] = {}
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            try:
                topic_id = int(item.get("topic_id"))
            except Exception:
                continue
            results_by_topic_id[topic_id] = self._normalize_llm_result(item)

        analyses: list[TopicAnalysis] = []
        for payload in payloads:
            analysis = results_by_topic_id.get(payload.topic_id)
            if analysis is None:
                analyses.append(
                    self._neutral_analysis(
                        summary="AI 响应缺少该主题结果。",
                        reason="模型返回的批量结果不完整。",
                        provider=f"llm_incomplete:{self.model_name}",
                    )
                )
                continue
            analyses.append(analysis)
        return analyses

    def _request_llm_content(self, request_payload: dict[str, Any]) -> str:
        if self._llm_http is None or self._llm_chat_url is None:
            raise RuntimeError("LLM HTTP client is unavailable.")

        response = self._llm_http.post(self._llm_chat_url, json=request_payload)
        response_text = response.text
        response_body: Any
        try:
            response_body = response.json()
        except ValueError:
            response_body = response_text

        if response.status_code >= 400:
            message = response_text
            if isinstance(response_body, dict):
                error = response_body.get("error")
                if isinstance(error, dict):
                    message = str(error.get("message") or message)
            raise LLMRequestError(
                status_code=response.status_code,
                message=message or f"HTTP {response.status_code}",
                body=response_body,
            )

        if not isinstance(response_body, dict):
            return response_text or "[]"
        return self._extract_llm_content(response_body)

    def _extract_llm_content(self, response_body: dict[str, Any]) -> str:
        choices = response_body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("LLM response did not contain choices.")
        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise ValueError("LLM response choice payload is invalid.")
        message = first_choice.get("message")
        if not isinstance(message, dict):
            raise ValueError("LLM response did not contain a message object.")
        content = message.get("content")
        if isinstance(content, str):
            return content or "[]"
        if isinstance(content, list):
            text_parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text")
                    if isinstance(text, str):
                        text_parts.append(text)
            if text_parts:
                return "".join(text_parts)
        return "[]"

    def _normalize_llm_result(self, parsed: dict[str, Any]) -> TopicAnalysis:
        labels = self._normalize_labels(parsed.get("labels"))
        primary_label = self._normalize_label(parsed.get("primary_label"))
        if primary_label == DEFAULT_TOPIC_LABEL and labels:
            primary_label = labels[0]
        if primary_label != DEFAULT_TOPIC_LABEL and primary_label not in labels:
            labels = [primary_label, *labels]
            labels = self._normalize_labels(labels)
        if primary_label == DEFAULT_TOPIC_LABEL and labels:
            primary_label = labels[0]
        if primary_label != DEFAULT_TOPIC_LABEL and not labels:
            labels = [primary_label]
        if not labels:
            labels = [primary_label or DEFAULT_TOPIC_LABEL]
        if not primary_label:
            primary_label = labels[0]

        reasons = self._normalize_reasons(parsed.get("reasons"))
        summary = self._normalize_summary(
            parsed.get("summary"),
            primary_label=primary_label,
            labels=labels,
        )
        requires_notification = self._normalize_requires_notification(parsed.get("requires_notification"))

        return TopicAnalysis(
            primary_label=primary_label,
            labels=labels,
            summary=summary,
            reasons=reasons,
            provider=f"llm:{self.model_name}",
            requires_notification=requires_notification,
        )

    def _build_prompt_payload(self, payload: TopicPayload) -> dict[str, Any]:
        return {
            "topic_id": payload.topic_id,
            "title": self._sanitize_llm_value(self._clip_text(payload.title, LLM_MAX_TITLE_CHARS)),
            "category_name": self._sanitize_llm_value(payload.category_name),
            "access_level": self._sanitize_llm_value(payload.access_level),
            "tags": normalize_topic_tags(payload.tags),
            "author_username": self._sanitize_llm_value(payload.author_username),
            "author_display_name": self._sanitize_llm_value(payload.author_display_name),
            "content_text": self._sanitize_llm_value(self._clip_text(payload.content_text, LLM_MAX_CONTENT_CHARS)),
            "external_links": self._sanitize_llm_value(payload.external_links),
            "reply_count": payload.reply_count,
            "like_count": payload.like_count,
            "view_count": payload.view_count,
            "word_count": payload.word_count,
        }

    def _build_user_content(self, *, payload: dict[str, Any]) -> str:
        return (
            "你是 Linux.do 主题识别助手。你会一次收到最多 10 条主题，必须逐条独立判断，不能互相污染。\n"
            "当前用户真正关心什么，由输入 JSON 里的 focus_keywords、focus_prompt、notification_prompt 决定。\n"
            "本站帖子类型很多，但用户只关心高密度信息，尤其是前沿、AI 相关、可执行经验和严谨实测，不关心无意义闲聊。\n"
            "你必须遵守以下规则：\n"
            "1. 正文证据优先于标题；标题与正文冲突时，以正文为准。\n"
            "2. 分类、标签、作者、外链、统计只做辅证，不能单独决定命中。\n"
            "3. 每条帖子都必须生成 1 到 3 个标签，primary_label 也必须是具体标签，不能输出 general 或空标签。\n"
            f"4. 标签优先从以下风格中选择：{', '.join(PREFERRED_TOPIC_TAGS)}。只有确实不适配时，才允许自造一个不超过 8 个字的中文短标签。\n"
            "5. 只要帖子和 AI / 模型 / 编程代理 / Codex / Claude Code 明显相关，就必须额外包含标签“AI相关”。\n"
            "6. 如果是 AI 相关新闻，优先打“前沿快讯”；如果是通过实践、对比、复现、测试去验证结论或打破谣言，且正文包含明确步骤、样例、对照、结果或结论，优先打“实验复现”或“辟谣实测”；如果是 Codex 或 Claude Code 的可操作使用小技巧、提效方法、避坑经验，且正文给出明确做法或经验结论，优先打“Codex技巧”或“ClaudeCode技巧”。纯提问不算技巧帖。\n"
            "7. 以下类型属于低价值排除项，除非正文同时含有高密度 AI 信息，否则不要通知：注册成功庆祝、升级庆祝、纯闲聊、女装整活、感情贴、树洞、生活琐事、单纯求安慰。\n"
            "8. 注册成功庆祝、刚进站报到、账号升级成功、生日打卡、征友搭子、情绪抒发这类，即使带一点 AI 词汇，也应优先归入“注册庆祝”“闲聊”“树洞”“感情话题”等排除标签。\n"
            "9. 是否推送只看 requires_notification 这一个字段，系统不会再根据 primary_label 或 labels 二次修正。你必须对这个字段独立负责；拿不准时一律 false。以下情况才明显倾向 true：AI相关+前沿快讯；AI相关+实验复现；AI相关+辟谣实测；真正给出明确做法和经验结论的 Codex技巧/ClaudeCode技巧；以及正文直接给出可执行福利或公益站信息，例如站点入口、API 地址、额度、密钥、模型范围、领取路径、使用方式、开放规则中的任意多项。普通求助、订阅纠结、注册庆祝、闲聊、水贴、纯吐槽都应为 false。\n"
            "10. 输出只能是顶层 JSON 数组，格式为 [{topic_id, primary_label, labels, summary, reasons, requires_notification}]。\n"
            "11. 不要 markdown、不要代码块、不要额外说明。\n\n"
            "输入数据(JSON):\n"
            f"{json.dumps(payload, ensure_ascii=False)}"
        )

    def _log_llm_response(
        self,
        *,
        kind: str,
        topic_ids: list[int],
        response_content: str,
    ) -> None:
        LOGGER.info(
            "LLM %s response | base_url=%s | model=%s | topic_ids=%s\n%s",
            kind,
            self._llm_base_url or "",
            self.model_name or "",
            topic_ids,
            self._truncate_for_log(response_content),
        )

    def _log_llm_request(
        self,
        *,
        kind: str,
        topic_ids: list[int],
        request_payload: dict[str, Any],
    ) -> None:
        LOGGER.info(
            "LLM %s request | base_url=%s | model=%s | topic_ids=%s\n%s",
            kind,
            self._llm_base_url or "",
            self.model_name or "",
            topic_ids,
            self._serialize_json(request_payload),
        )

    def _serialize_json(self, value: Any) -> str:
        try:
            return json.dumps(value, ensure_ascii=False, indent=2)
        except Exception:
            return repr(value)

    def _truncate_for_log(self, value: Any, limit: int = 12000) -> str:
        text = value if isinstance(value, str) else self._serialize_json(value)
        if len(text) <= limit:
            return text
        return f"{text[:limit]}\n... [truncated {len(text) - limit} chars]"

    def _serialize_llm_exception(self, exc: Exception) -> str:
        details: dict[str, Any] = {
            "type": type(exc).__name__,
            "message": str(exc),
        }
        for attr in ("status_code", "code", "param", "body"):
            value = getattr(exc, attr, None)
            if value is not None:
                details[attr] = value
        response = getattr(exc, "response", None)
        if response is not None:
            try:
                details["response_status_code"] = getattr(response, "status_code", None)
                details["response_text"] = response.text
            except Exception:
                pass
        return self._serialize_json(details)

    def _should_retry_with_smaller_batch(self, exc: Exception, payloads: list[TopicPayload]) -> bool:
        if len(payloads) <= 1:
            return False
        if isinstance(exc, LLMRequestError):
            return exc.status_code in {408, 429, 500, 502, 503, 504}
        return isinstance(
            exc,
            (
                httpx.TimeoutException,
                httpx.NetworkError,
                httpx.RemoteProtocolError,
            ),
        )

    def _sanitize_llm_value(self, value: Any) -> Any:
        if isinstance(value, str):
            redacted = value
            for index, pattern in enumerate(LLM_SECRET_PATTERNS):
                if index == 0:
                    redacted = pattern.sub(r"\1[REDACTED]", redacted)
                    continue
                redacted = pattern.sub("[REDACTED]", redacted)
            return redacted
        if isinstance(value, list):
            return [self._sanitize_llm_value(item) for item in value]
        if isinstance(value, dict):
            return {str(key): self._sanitize_llm_value(item) for key, item in value.items()}
        return value

    def _clip_text(self, value: Any, limit: int) -> Any:
        if not isinstance(value, str):
            return value
        text = value.strip()
        if len(text) <= limit:
            return text
        return f"{text[:limit]}\n[TRUNCATED]"

    def _parse_llm_json_value(self, content: str) -> Any:
        candidate = (content or "").strip()
        if not candidate:
            return []
        direct = self._try_parse_json_value(candidate)
        if direct is not None:
            return direct
        fenced_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", candidate, flags=re.IGNORECASE)
        if fenced_match:
            fenced = self._try_parse_json_value(fenced_match.group(1).strip())
            if fenced is not None:
                return fenced
        json_start = min((index for index in (candidate.find("{"), candidate.find("[")) if index >= 0), default=-1)
        if json_start >= 0:
            extracted = self._try_parse_json_value(candidate[json_start:].strip())
            if extracted is not None:
                return extracted
        raise ValueError("LLM response did not contain valid JSON content.")

    def _try_parse_json_value(self, candidate: str) -> Any | None:
        try:
            return json.loads(candidate)
        except Exception:
            pass
        try:
            decoder = json.JSONDecoder()
            parsed, _ = decoder.raw_decode(candidate)
            return parsed
        except Exception:
            return None

    def _normalize_reasons(self, value: Any) -> list[str]:
        if isinstance(value, list):
            reasons = [str(item).strip() for item in value if str(item).strip()]
            if reasons:
                return reasons[:6]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return ["模型未返回完整理由，当前按保守结果写入。"]

    def _normalize_summary(
        self,
        value: Any,
        *,
        primary_label: str,
        labels: list[str],
    ) -> str:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if primary_label in BLOCKED_NOTIFY_LABELS:
            return f"主题更接近{primary_label}，信息密度不足，不建议重点关注。"
        if primary_label == DEFAULT_TOPIC_LABEL:
            return "正文存在讨论价值，但未达到重点通知级别。"
        label_text = primary_label if primary_label != DEFAULT_TOPIC_LABEL else "当前关注点"
        if len(labels) > 1:
            return f"命中关注点：{label_text}，且存在多标签特征。"
        return f"命中关注点：{label_text}。"

    def _normalize_label(self, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return DEFAULT_TOPIC_LABEL
        lowered = text.lower()
        if lowered in LABEL_ALIASES:
            return LABEL_ALIASES[lowered]
        if text in LABEL_ALIASES:
            return LABEL_ALIASES[text]
        return text[:64]

    def _normalize_labels(self, value: Any) -> list[str]:
        if isinstance(value, str):
            items = [part.strip() for part in value.replace("，", ",").split(",")]
        elif isinstance(value, list):
            items = [str(item).strip() for item in value]
        else:
            return []

        labels: list[str] = []
        seen: set[str] = set()
        for item in items:
            normalized = self._normalize_label(item)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            labels.append(normalized[:64])
        return labels[:8]

    def _normalize_requires_notification(self, value: Any) -> bool:
        # Push decisions now come from this single field only.
        return value if isinstance(value, bool) else False

    def _batched(self, payloads: list[TopicPayload], batch_size: int) -> list[list[TopicPayload]]:
        if not payloads:
            return []
        return [payloads[index : index + batch_size] for index in range(0, len(payloads), batch_size)]
