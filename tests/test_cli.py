from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from linuxdoscanner import cli


class CLIMainTests(unittest.TestCase):
    def _settings(self) -> SimpleNamespace:
        return SimpleNamespace(
            bridge_host="127.0.0.1",
            bridge_port=8765,
            bridge_token=None,
            ensure_directories=lambda: None,
        )

    def test_bridge_server_uses_tray_on_windows_by_default(self) -> None:
        settings = self._settings()
        startup_manager = SimpleNamespace(default_browser_url=lambda: "https://linux.do/latest?order=created")
        server = SimpleNamespace(close=lambda: None)

        with patch("linuxdoscanner.cli.bootstrap_bundled_directory"):
            with patch("linuxdoscanner.cli.Settings.from_env", return_value=settings):
                with patch("linuxdoscanner.cli.configure_logging", return_value=("info.log", "error.log")):
                    with patch("linuxdoscanner.cli.WindowsStartupManager", return_value=startup_manager):
                        with patch("linuxdoscanner.cli.ExtensionBridgeServer", return_value=server):
                            with patch("linuxdoscanner.cli._run_server_with_tray", return_value=0) as run_with_tray:
                                with patch("linuxdoscanner.cli._run_server_forever", return_value=0) as run_forever:
                                    with patch("linuxdoscanner.cli.os.name", "nt"):
                                        exit_code = cli.main(["bridge-server"])

        self.assertEqual(exit_code, 0)
        run_with_tray.assert_called_once_with(server, settings)
        run_forever.assert_not_called()

    def test_bridge_server_can_disable_tray(self) -> None:
        settings = self._settings()
        startup_manager = SimpleNamespace(default_browser_url=lambda: "https://linux.do/latest?order=created")
        server = SimpleNamespace(close=lambda: None)

        with patch("linuxdoscanner.cli.bootstrap_bundled_directory"):
            with patch("linuxdoscanner.cli.Settings.from_env", return_value=settings):
                with patch("linuxdoscanner.cli.configure_logging", return_value=("info.log", "error.log")):
                    with patch("linuxdoscanner.cli.WindowsStartupManager", return_value=startup_manager):
                        with patch("linuxdoscanner.cli.ExtensionBridgeServer", return_value=server):
                            with patch("linuxdoscanner.cli._run_server_with_tray", return_value=0) as run_with_tray:
                                with patch("linuxdoscanner.cli._run_server_forever", return_value=0) as run_forever:
                                    with patch("linuxdoscanner.cli.os.name", "nt"):
                                        exit_code = cli.main(["bridge-server", "--no-tray"])

        self.assertEqual(exit_code, 0)
        run_forever.assert_called_once_with(server)
        run_with_tray.assert_not_called()


if __name__ == "__main__":
    unittest.main()
