from __future__ import annotations

import sys
import unittest
from unittest.mock import patch

from linuxdoscanner.bridge import ExtensionBridgeServer, _current_process_restart_args


class BridgeRestartTests(unittest.TestCase):
    def test_restart_args_keep_script_invocation_for_source_run(self) -> None:
        with patch.object(sys, "executable", "python.exe"):
            with patch.object(sys, "argv", ["main.py", "bridge-server"]):
                with patch.object(sys, "frozen", False, create=True):
                    executable, args = _current_process_restart_args()

        self.assertEqual(executable, "python.exe")
        self.assertEqual(args, ["python.exe", "main.py", "bridge-server"])

    def test_restart_args_do_not_duplicate_frozen_executable(self) -> None:
        with patch.object(sys, "executable", "LinuxDoScannerBackend.exe"):
            with patch.object(sys, "argv", ["LinuxDoScannerBackend.exe", "startup-run"]):
                with patch.object(sys, "frozen", True, create=True):
                    executable, args = _current_process_restart_args()

        self.assertEqual(executable, "LinuxDoScannerBackend.exe")
        self.assertEqual(args, ["LinuxDoScannerBackend.exe", "startup-run"])

    def test_schedule_restart_uses_non_daemon_thread(self) -> None:
        created: dict[str, object] = {}

        class FakeThread:
            def __init__(self, *, target, name: str, daemon: bool) -> None:
                created["target"] = target
                created["name"] = name
                created["daemon"] = daemon

            def start(self) -> None:
                created["started"] = True

        events: list[str] = []
        server = object.__new__(ExtensionBridgeServer)
        server.stop = lambda: events.append("stop")
        server._replace_current_process = lambda: events.append("replace")

        with patch("linuxdoscanner.bridge.threading.Thread", FakeThread):
            server._schedule_restart()

        self.assertEqual(created["name"], "linuxdoscanner-restart")
        self.assertFalse(created["daemon"])
        self.assertTrue(created["started"])

        with patch("time.sleep") as sleep:
            target = created["target"]
            self.assertTrue(callable(target))
            target()

        sleep.assert_called_once_with(1)
        self.assertEqual(events, ["stop", "replace"])


if __name__ == "__main__":
    unittest.main()
