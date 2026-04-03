from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from linuxdoscanner.notification_config import NotificationConfig
from linuxdoscanner.notify import FeishuNotifier, WindowsToastNotifier, _hidden_subprocess_kwargs


class FeishuNotifierTests(unittest.TestCase):
    def _build_notifier(self, config: NotificationConfig) -> FeishuNotifier:
        return FeishuNotifier(SimpleNamespace(), config=config)

    def test_build_markdown_body_keeps_all_metadata(self) -> None:
        notifier = self._build_notifier(
            NotificationConfig(
                feishu_enabled=True,
                lark_cli_path="C:/Program Files/nodejs/lark-cli.cmd",
                feishu_user_id="ou_test",
            )
        )
        topic = {
            "ai_labels_json": '["模型更新", "AI相关"]',
            "ai_reasons_json": '["正文提到模型能力变化", "包含直接体验"]',
            "title": "感觉deepseek是要更新了？猜的",
            "author_display_name": "xiaohan",
            "author_username": "xiaohanQWQ",
            "category_name": "搞七捻三",
            "ai_summary": "正文给出了可观察到的模型行为变化。",
            "ai_label": "模型更新",
            "url": "https://linux.do/t/topic/1847435",
        }

        body = notifier._build_markdown_body(topic)

        self.assertIn("【模型更新】感觉deepseek是要更新了？猜的", body)
        self.assertIn("作者：xiaohan", body)
        self.assertIn("分类：搞七捻三", body)
        self.assertIn("标签：模型更新, AI相关", body)
        self.assertIn("摘要：正文给出了可观察到的模型行为变化。", body)
        self.assertIn("原因：正文提到模型能力变化；包含直接体验", body)
        self.assertIn("链接：https://linux.do/t/topic/1847435", body)

    def test_send_markdown_bypasses_windows_cmd_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cli_path = root / "lark-cli.cmd"
            cli_path.write_text("@echo off\r\n", encoding="utf-8")
            node_executable = root / "node.exe"
            node_executable.write_bytes(b"")
            run_script = root / "node_modules" / "@larksuite" / "cli" / "scripts" / "run.js"
            run_script.parent.mkdir(parents=True, exist_ok=True)
            run_script.write_text("console.log('stub')\n", encoding="utf-8")

            notifier = self._build_notifier(
                NotificationConfig(
                    feishu_enabled=True,
                    lark_cli_path=str(cli_path),
                    feishu_user_id="ou_test",
                )
            )

            with patch("linuxdoscanner.notify.os.name", "nt"):
                with patch("linuxdoscanner.notify.subprocess.run") as subprocess_run:
                    notifier.send_markdown("第一行\n第二行")

            command = subprocess_run.call_args.args[0]
            self.assertEqual(command[0], str(node_executable))
            self.assertEqual(command[1], str(run_script))
            self.assertEqual(command[-2:], ["--markdown", "第一行\n第二行"])

    def test_send_markdown_uses_hidden_windows_subprocess(self) -> None:
        notifier = self._build_notifier(
            NotificationConfig(
                feishu_enabled=True,
                lark_cli_path="lark-cli",
                feishu_user_id="ou_test",
            )
        )

        with patch("linuxdoscanner.notify._hidden_subprocess_kwargs", return_value={"creationflags": 123}) as hidden_kwargs:
            with patch("linuxdoscanner.notify.subprocess.run") as subprocess_run:
                notifier.send_markdown("hello")

        hidden_kwargs.assert_called_once_with()
        self.assertEqual(subprocess_run.call_args.kwargs["creationflags"], 123)

    def test_send_continues_after_single_topic_failure(self) -> None:
        notifier = self._build_notifier(
            NotificationConfig(
                feishu_enabled=True,
                lark_cli_path="lark-cli",
                feishu_user_id="ou_test",
            )
        )
        topics = [
            {"topic_id": 1},
            {"topic_id": 2},
        ]

        with patch.object(
            notifier,
            "_send_topic",
            side_effect=[RuntimeError("boom"), None],
        ):
            sent_ids = notifier.send(topics)

        self.assertEqual(sent_ids, [2])


class WindowsToastNotifierTests(unittest.TestCase):
    def test_hidden_subprocess_kwargs_builds_windows_flags(self) -> None:
        class DummyStartupInfo:
            def __init__(self) -> None:
                self.dwFlags = 0
                self.wShowWindow = 1

        with patch("linuxdoscanner.notify.os.name", "nt"):
            with patch.object(subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True):
                with patch.object(subprocess, "STARTUPINFO", DummyStartupInfo, create=True):
                    with patch.object(subprocess, "STARTF_USESHOWWINDOW", 1, create=True):
                        kwargs = _hidden_subprocess_kwargs()

        self.assertEqual(kwargs["creationflags"], 0x08000000)
        self.assertEqual(kwargs["startupinfo"].dwFlags, 1)
        self.assertEqual(kwargs["startupinfo"].wShowWindow, 0)

    def test_send_continues_after_single_topic_failure(self) -> None:
        notifier = WindowsToastNotifier(SimpleNamespace(windows_notifications_enabled=True))
        topics = [
            {"topic_id": 1},
            {"topic_id": 2},
        ]

        with patch("linuxdoscanner.notify.os.name", "nt"):
            with patch.object(
                notifier,
                "_show_toast",
                side_effect=[RuntimeError("boom"), None],
            ):
                sent_ids = notifier.send(topics)

        self.assertEqual(sent_ids, [2])


if __name__ == "__main__":
    unittest.main()
