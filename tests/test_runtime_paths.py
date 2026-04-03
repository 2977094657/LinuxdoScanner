from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from linuxdoscanner import runtime_paths


class RuntimePathsTests(unittest.TestCase):
    def test_app_root_uses_source_root_when_not_frozen(self) -> None:
        with patch("linuxdoscanner.runtime_paths.is_frozen", return_value=False):
            self.assertEqual(runtime_paths.app_root(), runtime_paths.source_root())

    def test_app_root_uses_executable_directory_when_frozen(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executable_path = Path(temp_dir) / "LinuxDoScannerBackend.exe"
            executable_path.write_text("", encoding="utf-8")
            with patch("linuxdoscanner.runtime_paths.is_frozen", return_value=True):
                with patch("sys.executable", str(executable_path)):
                    self.assertEqual(runtime_paths.app_root(), executable_path.parent.resolve())

    def test_bootstrap_bundled_directory_copies_into_app_root_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            source_dir = temp_root / "_internal" / "config"
            source_dir.mkdir(parents=True, exist_ok=True)
            (source_dir / "settings.toml").write_text("hello = 'world'\n", encoding="utf-8")
            destination_root = temp_root / "app"
            destination_root.mkdir(parents=True, exist_ok=True)

            with patch("linuxdoscanner.runtime_paths.app_root", return_value=destination_root):
                with patch("linuxdoscanner.runtime_paths.bundle_root", return_value=temp_root / "_internal"):
                    target_dir = runtime_paths.bootstrap_bundled_directory("config")

            self.assertEqual(target_dir, destination_root / "config")
            self.assertTrue((target_dir / "settings.toml").exists())
