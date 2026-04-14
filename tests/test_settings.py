from __future__ import annotations

import logging
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from loguru import logger

from linuxdoscanner.logging_utils import configure_logging
from linuxdoscanner.settings import Settings, detect_lark_cli_executable


class SettingsConfigTests(unittest.TestCase):
    def tearDown(self) -> None:
        logger.remove()
        logging.getLogger().handlers.clear()

    def test_from_env_loads_toml_paths_and_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            config_dir = project_root / "config"
            config_dir.mkdir(parents=True, exist_ok=True)
            (config_dir / "settings.toml").write_text(
                """
[paths]
output_dir = "custom-output"
database_dir = ".runtime/sqlite"
browser_dir = ".runtime/browser"

[app]
bridge_port = 9999
bootstrap_limit = 66

[crawl]
max_pages_per_run = 22
page_request_delay_min_seconds = 3
page_request_delay_max_seconds = 9
round_delay_min_seconds = 7
round_delay_max_seconds = 21

[browser]
cookie_source = "manual"

[llm]
model = "example/model"
                """.strip(),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=True):
                settings = Settings.from_env(project_root=project_root)

            self.assertEqual(settings.output_dir, (project_root / "custom-output").resolve())
            self.assertEqual(settings.database_path, (project_root / ".runtime/sqlite/linuxdo.sqlite3").resolve())
            self.assertEqual(settings.browser_root_dir, (project_root / ".runtime/browser").resolve())
            self.assertEqual(settings.bridge_port, 9999)
            self.assertEqual(settings.bootstrap_limit, 66)
            self.assertEqual(settings.max_pages_per_run, 22)
            self.assertEqual(settings.page_request_delay_min_seconds, 3)
            self.assertEqual(settings.page_request_delay_max_seconds, 9)
            self.assertEqual(settings.round_delay_min_seconds, 7)
            self.assertEqual(settings.round_delay_max_seconds, 21)
            self.assertEqual(settings.browser_cookie_source, "manual")
            self.assertEqual(settings.openai_model, "example/model")

    def test_stateful_defaults_follow_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            config_dir = project_root / "config"
            config_dir.mkdir(parents=True, exist_ok=True)
            (config_dir / "settings.toml").write_text(
                """
[paths]
output_dir = "custom-output"
                """.strip(),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=True):
                settings = Settings.from_env(project_root=project_root)

            expected_output_dir = (project_root / "custom-output").resolve()
            self.assertEqual(settings.output_dir, expected_output_dir)
            self.assertEqual(settings.state_dir, expected_output_dir)
            self.assertEqual(settings.database_path, (expected_output_dir / "databases/linuxdo.sqlite3").resolve())
            self.assertEqual(settings.browser_root_dir, (expected_output_dir / "browser").resolve())

    def test_env_overrides_toml_and_log_files_are_split(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            config_dir = project_root / "config"
            config_dir.mkdir(parents=True, exist_ok=True)
            config_path = config_dir / "settings.toml"
            config_path.write_text(
                """
[app]
bridge_port = 1234
                """.strip(),
                encoding="utf-8",
            )

            with patch.dict(
                os.environ,
                {
                    "LINUXDO_CONFIG_FILE": str(config_path),
                    "LINUXDO_BRIDGE_PORT": "8765",
                },
                clear=True,
            ):
                settings = Settings.from_env(project_root=project_root)

            settings.ensure_directories()
            info_log_path, error_log_path = configure_logging(
                debug=False,
                settings=settings,
                now=datetime(2026, 4, 1, 10, 30, 0),
            )

            app_logger = logging.getLogger("tests.settings")
            app_logger.info("hello info")
            app_logger.error("hello error")

            self.assertEqual(settings.bridge_port, 8765)
            self.assertEqual(
                info_log_path,
                (project_root / "output/logs/2026/04/01/01_info.log").resolve(),
            )
            self.assertEqual(
                error_log_path,
                (project_root / "output/logs/2026/04/01/01_error.log").resolve(),
            )

            info_content = info_log_path.read_text(encoding="utf-8")
            error_content = error_log_path.read_text(encoding="utf-8")
            self.assertIn("hello info", info_content)
            self.assertNotIn("hello error", info_content)
            self.assertIn("hello error", error_content)
            logger.remove()
            logging.getLogger().handlers.clear()

    def test_legacy_hidden_output_is_migrated_into_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            legacy_output_dir = project_root / ".output"
            legacy_database_path = legacy_output_dir / "databases" / "linuxdo.sqlite3"
            legacy_browser_state_path = legacy_output_dir / "browser" / "storage_state.json"
            legacy_session_meta_path = legacy_output_dir / "browser" / "session_meta.json"
            legacy_database_path.parent.mkdir(parents=True, exist_ok=True)
            legacy_browser_state_path.parent.mkdir(parents=True, exist_ok=True)
            legacy_database_path.write_text("legacy database", encoding="utf-8")
            legacy_browser_state_path.write_text('{"cookies": []}', encoding="utf-8")
            legacy_session_meta_path.write_text('{"status": "legacy"}', encoding="utf-8")

            with patch.dict(os.environ, {}, clear=True):
                settings = Settings.from_env(project_root=project_root)

            settings.ensure_directories()

            self.assertEqual(settings.output_dir, (project_root / "output").resolve())
            self.assertEqual(settings.state_dir, settings.output_dir)
            self.assertEqual(settings.database_path.read_text(encoding="utf-8"), "legacy database")
            self.assertEqual(settings.storage_state_path.read_text(encoding="utf-8"), '{"cookies": []}')
            self.assertEqual(settings.session_meta_path.read_text(encoding="utf-8"), '{"status": "legacy"}')

    def test_configure_logging_handles_missing_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            config_dir = project_root / "config"
            config_dir.mkdir(parents=True, exist_ok=True)
            (config_dir / "settings.toml").write_text("", encoding="utf-8")

            with patch.dict(os.environ, {}, clear=True):
                settings = Settings.from_env(project_root=project_root)

            settings.ensure_directories()
            with patch("sys.stderr", None):
                info_log_path, error_log_path = configure_logging(
                    debug=False,
                    settings=settings,
                    now=datetime(2026, 4, 1, 10, 30, 0),
                )
                app_logger = logging.getLogger("tests.settings.background")
                app_logger.info("background info")
                app_logger.error("background error")

            self.assertIn("background info", info_log_path.read_text(encoding="utf-8"))
            self.assertIn("background error", error_log_path.read_text(encoding="utf-8"))
            logger.remove()
            logging.getLogger().handlers.clear()

    def test_detect_lark_cli_skips_inaccessible_candidates(self) -> None:
        def fake_exists(path: Path) -> bool:
            if str(path) == "blocked":
                raise PermissionError("blocked")
            return str(path) == "available"

        with patch("linuxdoscanner.settings.Path.exists", fake_exists):
            with patch.dict(os.environ, {"LARK_CLI_PATH": "available"}, clear=True):
                self.assertEqual(detect_lark_cli_executable("blocked"), "available")
