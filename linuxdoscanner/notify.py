from __future__ import annotations

import base64
import json
import logging
import os
import shutil
import smtplib
import subprocess
from email.message import EmailMessage
from pathlib import Path
from sqlite3 import Row
from typing import Iterable

from .notification_config import NotificationConfig
from .settings import Settings


LOGGER = logging.getLogger(__name__)


def _hidden_subprocess_kwargs() -> dict[str, object]:
    if os.name != "nt":
        return {}

    kwargs: dict[str, object] = {}
    creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if creationflags:
        kwargs["creationflags"] = creationflags

    startupinfo_factory = getattr(subprocess, "STARTUPINFO", None)
    if startupinfo_factory is not None:
        startupinfo = startupinfo_factory()
        startupinfo.dwFlags |= int(getattr(subprocess, "STARTF_USESHOWWINDOW", 0))
        startupinfo.wShowWindow = 0
        kwargs["startupinfo"] = startupinfo

    return kwargs


class EmailNotifier:
    def __init__(self, settings: Settings):
        self.settings = settings

    def is_configured(self) -> bool:
        return all(
            [
                self.settings.smtp_host,
                self.settings.smtp_sender,
                self.settings.smtp_recipient,
            ]
        )

    def send(self, topics: Iterable[Row]) -> list[int]:
        topics = list(topics)
        if not topics or not self.is_configured():
            return []

        message = EmailMessage()
        message["Subject"] = f"[linux.do] 发现 {len(topics)} 条值得关注的新主题"
        message["From"] = self.settings.smtp_sender
        message["To"] = self.settings.smtp_recipient
        message.set_content(self._build_body(topics))

        if self.settings.smtp_use_tls:
            with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=30) as server:
                server.starttls()
                self._login(server)
                server.send_message(message)
        else:
            with smtplib.SMTP_SSL(
                self.settings.smtp_host,
                self.settings.smtp_port,
                timeout=30,
            ) as server:
                self._login(server)
                server.send_message(message)

        return [int(topic["topic_id"]) for topic in topics]

    def _login(self, server: smtplib.SMTP) -> None:
        if self.settings.smtp_username and self.settings.smtp_password:
            server.login(self.settings.smtp_username, self.settings.smtp_password)

    def _build_body(self, topics: list[Row]) -> str:
        lines = [
            "Linux.do 监控发现以下新主题值得关注：",
            "",
        ]
        for topic in topics:
            reasons = json.loads(topic["ai_reasons_json"] or "[]")
            labels = json.loads(topic["ai_labels_json"] or "[]")
            lines.extend(
                [
                    f"标题: {topic['title']}",
                    f"作者: {topic['author_display_name'] or topic['author_username'] or '未知'}",
                    f"分类: {topic['category_name'] or '未知'}",
                    f"主标签: {topic['ai_label'] or 'general'} | 多标签: {', '.join(labels) if labels else '无'}",
                    f"链接: {topic['url']}",
                    f"摘要: {topic['ai_summary'] or ''}",
                    f"原因: {'; '.join(reasons) if reasons else '无'}",
                    "",
                ]
            )
        return "\n".join(lines)


class WindowsToastNotifier:
    def __init__(self, settings: Settings):
        self.settings = settings

    def is_configured(self) -> bool:
        return self.settings.windows_notifications_enabled and os.name == "nt"

    def send(self, topics: Iterable[Row]) -> list[int]:
        topics = list(topics)
        if not topics or not self.is_configured():
            return []

        sent_ids: list[int] = []
        errors: list[str] = []
        for topic in topics:
            try:
                self._show_toast(topic)
            except Exception as exc:
                topic_id = int(topic["topic_id"])
                LOGGER.warning("Windows toast notification failed for topic %s: %s", topic_id, exc)
                errors.append(f"{topic_id}:{exc}")
                continue
            sent_ids.append(int(topic["topic_id"]))
        if errors and not sent_ids:
            raise RuntimeError("; ".join(errors))
        return sent_ids

    def _show_toast(self, topic: Row) -> None:
        title = f"[linux.do] {topic['ai_label'] or '命中'}"
        body = "\n".join(
            [
                str(topic["title"] or "")[:72],
                f"作者: {topic['author_display_name'] or topic['author_username'] or '未知'}",
                f"分类: {topic['category_name'] or '未知'}",
            ]
        )
        script = self._build_powershell_script(title=title, body=body)
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-EncodedCommand",
                encoded,
            ],
            check=True,
            timeout=15,
            **_hidden_subprocess_kwargs(),
        )

    def _build_powershell_script(self, *, title: str, body: str) -> str:
        safe_title = title.replace("'@", "' @")
        safe_body = body.replace("'@", "' @")
        return f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null
$title = @'
{safe_title}
'@
$body = @'
{safe_body}
'@
$titleEsc = [System.Security.SecurityElement]::Escape($title)
$bodyEsc = [System.Security.SecurityElement]::Escape($body)
$template = "<toast><visual><binding template='ToastGeneric'><text>$titleEsc</text><text>$bodyEsc</text></binding></visual></toast>"
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($template)
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('LinuxDoScanner')
$notifier.Show($toast)
"""


class FeishuNotifier:
    def __init__(self, settings: Settings, config: NotificationConfig | None = None):
        self.settings = settings
        self.config = config or NotificationConfig.from_settings(settings)

    def is_configured(self) -> bool:
        return self.config.is_feishu_configured

    def send(self, topics: Iterable[Row]) -> list[int]:
        topics = list(topics)
        if not topics or not self.is_configured():
            return []

        sent_ids: list[int] = []
        errors: list[str] = []
        for topic in topics:
            try:
                self._send_topic(topic)
            except Exception as exc:
                topic_id = int(topic["topic_id"])
                LOGGER.warning("Feishu notification failed for topic %s: %s", topic_id, exc)
                errors.append(f"{topic_id}:{exc}")
                continue
            sent_ids.append(int(topic["topic_id"]))
        if errors and not sent_ids:
            raise RuntimeError("; ".join(errors))
        return sent_ids

    def _send_topic(self, topic: Row) -> None:
        self.send_markdown(self._build_markdown_body(topic))

    def send_text(self, body: str) -> None:
        if not self.is_configured():
            raise RuntimeError("飞书通知尚未配置完整。")

        command = self._build_cli_command_prefix() + [
            "im",
            "+messages-send",
            "--as",
            "bot",
        ]
        if self.config.feishu_chat_id:
            command.extend(["--chat-id", self.config.feishu_chat_id])
        else:
            command.extend(["--user-id", self.config.feishu_user_id or ""])
        command.extend(["--text", body])

        subprocess.run(
            command,
            check=True,
            timeout=30,
            **_hidden_subprocess_kwargs(),
        )

    def send_markdown(self, body: str) -> None:
        if not self.is_configured():
            raise RuntimeError("飞书通知尚未配置完整。")

        command = self._build_cli_command_prefix() + [
            "im",
            "+messages-send",
            "--as",
            "bot",
        ]
        if self.config.feishu_chat_id:
            command.extend(["--chat-id", self.config.feishu_chat_id])
        else:
            command.extend(["--user-id", self.config.feishu_user_id or ""])
        command.extend(["--markdown", body])

        subprocess.run(
            command,
            check=True,
            timeout=30,
            **_hidden_subprocess_kwargs(),
        )

    def _build_markdown_body(self, topic: Row) -> str:
        labels = json.loads(topic["ai_labels_json"] or "[]")
        reasons = json.loads(topic["ai_reasons_json"] or "[]")
        title = self._single_line_text(topic["title"], limit=180)
        author = self._single_line_text(topic["author_display_name"] or topic["author_username"] or "未知", limit=80)
        category = self._single_line_text(topic["category_name"] or "未知", limit=80)
        summary = self._single_line_text(topic["ai_summary"], limit=220)
        label_text = self._single_line_text(
            ", ".join(str(item) for item in labels) if labels else (topic["ai_label"] or "未分类"),
            limit=120,
        )
        reasons_text = self._single_line_text(
            "；".join(str(item) for item in reasons[:3]) if reasons else "无",
            limit=220,
        )
        return "\n".join(
            [
                f"【{topic['ai_label'] or '命中'}】{title}",
                "",
                f"作者：{author}",
                f"分类：{category}",
                f"标签：{label_text}",
                f"摘要：{summary or '无'}",
                f"原因：{reasons_text}",
                f"链接：{topic['url']}",
            ]
        )

    def _single_line_text(self, value: object, *, limit: int) -> str:
        text = " ".join(str(value or "").split())
        if len(text) <= limit:
            return text
        return f"{text[: max(0, limit - 1)].rstrip()}…"

    def _build_cli_command_prefix(self) -> list[str]:
        cli_path = str(self.config.lark_cli_path or "lark-cli").strip() or "lark-cli"
        resolved_cli = shutil.which(cli_path) or cli_path
        cli_file = Path(resolved_cli)

        # On Windows, passing multiline arguments through the .cmd shim truncates the body
        # at the first newline before lark-cli sees it, so call the Node entrypoint directly.
        if os.name == "nt" and cli_file.suffix.lower() == ".cmd" and cli_file.exists():
            node_executable = cli_file.with_name("node.exe")
            run_script = cli_file.parent / "node_modules" / "@larksuite" / "cli" / "scripts" / "run.js"
            if run_script.exists():
                if not node_executable.exists():
                    node_executable = Path(shutil.which("node") or "node")
                return [str(node_executable), str(run_script)]

        return [cli_path]


class NotificationDispatcher:
    def __init__(self, settings: Settings, notification_config: NotificationConfig | None = None):
        self.settings = settings
        self.notification_config = notification_config or NotificationConfig.from_settings(settings)
        self.email = EmailNotifier(settings)
        self.feishu = FeishuNotifier(settings, self.notification_config)
        self.windows = WindowsToastNotifier(settings)

    def is_configured(self) -> bool:
        return self.email.is_configured() or self.feishu.is_configured() or self.windows.is_configured()

    def send(self, topics: Iterable[Row]) -> list[int]:
        topics = list(topics)
        if not topics or not self.is_configured():
            return []

        sent_ids: set[int] = set()
        errors: list[str] = []

        if self.email.is_configured():
            try:
                sent_ids.update(self.email.send(topics))
            except Exception as exc:
                LOGGER.warning("Email notification failed: %s", exc)
                errors.append(f"email:{exc}")

        if self.feishu.is_configured():
            try:
                sent_ids.update(self.feishu.send(topics))
            except Exception as exc:
                LOGGER.warning("Feishu notification failed: %s", exc)
                errors.append(f"feishu:{exc}")

        if self.windows.is_configured():
            try:
                sent_ids.update(self.windows.send(topics))
            except Exception as exc:
                LOGGER.warning("Windows toast notification failed: %s", exc)
                errors.append(f"windows:{exc}")

        if errors and not sent_ids:
            raise RuntimeError("; ".join(errors))
        return sorted(sent_ids)

    def send_test_message(self) -> None:
        message = (
            "LinuxDoScanner 飞书通知测试\n\n"
            "如果你收到了这条消息，说明扩展内保存的飞书配置已经生效。\n"
            "这条消息现在会走和正式通知相同的多行格式发送链路。"
        )
        self.feishu.send_markdown(message)
