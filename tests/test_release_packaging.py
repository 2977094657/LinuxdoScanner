from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path


def load_module(module_name: str, relative_path: str):
    project_root = Path(__file__).resolve().parent.parent
    module_path = project_root / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ReleasePackagingTests(unittest.TestCase):
    def test_package_extension_updates_manifest_version_and_creates_zip(self) -> None:
        module = load_module("package_extension_script", "scripts/package_extension.py")
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            extension_dir = project_root / "chrome-extension"
            extension_dir.mkdir(parents=True, exist_ok=True)
            (extension_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "manifest_version": 3,
                        "name": "LinuxDoScanner Bridge",
                        "version": "0.1.0",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (extension_dir / "popup.html").write_text("<html></html>\n", encoding="utf-8")

            archive_path = module.package_extension(
                project_root=project_root,
                tag_or_version="v1.2.3",
                output_dir=project_root / "dist",
            )

            self.assertEqual(archive_path.name, "LinuxDoScannerExtension-v1.2.3.zip")
            manifest = json.loads(
                (project_root / "dist" / "LinuxDoScannerExtension-v1.2.3" / "manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(manifest["version"], "1.2.3")
            with zipfile.ZipFile(archive_path) as archive:
                self.assertIn("LinuxDoScannerExtension-v1.2.3/manifest.json", archive.namelist())

    def test_package_backend_creates_versioned_zip(self) -> None:
        module = load_module("package_backend_script", "scripts/package_backend.py")
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            backend_dir = project_root / "dist" / "LinuxDoScannerBackend"
            backend_dir.mkdir(parents=True, exist_ok=True)
            (backend_dir / "LinuxDoScannerBackend.exe").write_text("binary", encoding="utf-8")
            (backend_dir / "config.ini").write_text("hello=world\n", encoding="utf-8")

            archive_path = module.package_backend(
                project_root=project_root,
                tag_or_version="v2.0.0",
                output_dir=project_root / "dist",
            )

            self.assertEqual(archive_path.name, "LinuxDoScannerBackend-v2.0.0-windows-x64.zip")
            with zipfile.ZipFile(archive_path) as archive:
                self.assertIn("LinuxDoScannerBackend/LinuxDoScannerBackend.exe", archive.namelist())
                self.assertIn("LinuxDoScannerBackend/config.ini", archive.namelist())


if __name__ == "__main__":
    unittest.main()
