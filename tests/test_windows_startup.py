from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from linuxdoscanner.settings import Settings
from linuxdoscanner.windows_startup import STARTUP_SCRIPT_MARKER, WindowsStartupManager


class WindowsStartupManagerTests(unittest.TestCase):
    def _make_settings(self, project_root: Path) -> Settings:
        (project_root / "main.py").write_text("print('linuxdoscanner')\n", encoding="utf-8")
        config_dir = project_root / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "settings.toml").write_text(
            """
[app]
bridge_host = "127.0.0.1"
bridge_port = 8765
            """.strip(),
            encoding="utf-8",
        )
        with patch.dict(os.environ, {}, clear=True):
            return Settings.from_env(project_root=project_root)

    def _make_manager(
        self,
        project_root: Path,
        *,
        with_browser: bool = True,
    ) -> WindowsStartupManager:
        settings = self._make_settings(project_root)
        scripts_dir = project_root / ".venv" / "Scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        python_executable = scripts_dir / "python.exe"
        pythonw_executable = scripts_dir / "pythonw.exe"
        python_executable.write_text("", encoding="utf-8")
        pythonw_executable.write_text("", encoding="utf-8")

        if with_browser:
            browser_executable = project_root / "browser" / "chrome.exe"
            browser_executable.parent.mkdir(parents=True, exist_ok=True)
            browser_executable.write_text("", encoding="utf-8")
            settings.browser_executable = str(browser_executable.resolve())
        else:
            settings.browser_executable = None

        return WindowsStartupManager(
            settings,
            python_executable=str(python_executable),
            appdata_dir=project_root / "AppData" / "Roaming",
            platform_name="nt",
        )

    def test_install_creates_startup_script_with_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            manager = self._make_manager(project_root)

            status = manager.install(
                launch_browser=True,
                browser_url="https://linux.do/latest?order=created",
            )

            self.assertTrue(status.enabled)
            self.assertTrue(status.use_tray)
            self.assertTrue(status.launch_browser)
            self.assertEqual(status.browser_url, "https://linux.do/latest?order=created")
            self.assertIsNotNone(status.script_path)
            script_content = status.script_path.read_text(encoding="utf-8-sig")
            self.assertIn(STARTUP_SCRIPT_MARKER, script_content)
            self.assertIn("startup-run --launch-browser", script_content)
            self.assertNotIn("--no-tray", script_content)
            self.assertIn("pythonw.exe", script_content.lower())

            reloaded_status = manager.status()
            self.assertTrue(reloaded_status.enabled)
            self.assertTrue(reloaded_status.use_tray)
            self.assertTrue(reloaded_status.launch_browser)
            self.assertEqual(reloaded_status.browser_url, "https://linux.do/latest?order=created")

    def test_install_can_disable_tray(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            manager = self._make_manager(project_root)

            status = manager.install(
                use_tray=False,
                launch_browser=False,
            )

            self.assertTrue(status.enabled)
            self.assertFalse(status.use_tray)
            self.assertIsNotNone(status.script_path)
            script_content = status.script_path.read_text(encoding="utf-8-sig")
            self.assertIn("startup-run --no-tray", script_content)

            reloaded_status = manager.status()
            self.assertFalse(reloaded_status.use_tray)

    def test_remove_deletes_existing_startup_script(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            manager = self._make_manager(project_root)
            manager.install(launch_browser=False)

            status = manager.remove()

            self.assertFalse(status.enabled)
            self.assertIsNotNone(manager.script_path)
            self.assertFalse(manager.script_path.exists())

    def test_launch_browser_requires_detected_browser_executable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            manager = self._make_manager(project_root, with_browser=False)

            with self.assertRaisesRegex(RuntimeError, "未检测到 Chrome 或 Edge"):
                manager.install(launch_browser=True)

    def test_non_windows_status_reports_unsupported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            manager = self._make_manager(project_root)
            manager.platform_name = "posix"

            status = manager.status()

            self.assertFalse(status.supported)
            self.assertFalse(status.enabled)
            self.assertTrue(status.use_tray)
            self.assertIn("Windows", status.reason)

    def test_install_uses_exe_directly_when_running_as_frozen_app(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            manager = self._make_manager(project_root)
            frozen_executable = project_root / "dist" / "LinuxDoScannerBackend.exe"
            frozen_executable.parent.mkdir(parents=True, exist_ok=True)
            frozen_executable.write_text("", encoding="utf-8")

            with patch("linuxdoscanner.windows_startup.is_frozen", return_value=True):
                with patch("linuxdoscanner.windows_startup.current_executable", return_value=frozen_executable):
                    status = manager.install(launch_browser=False)

            self.assertTrue(status.enabled)
            script_content = status.script_path.read_text(encoding="utf-8-sig")
            self.assertIn("LinuxDoScannerBackend.exe startup-run", script_content)
            self.assertNotIn("main.py", script_content)
