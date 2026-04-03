from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    try:
        import PyInstaller.__main__
    except ImportError:
        print("PyInstaller is not installed. Run: uv sync --extra build", file=sys.stderr)
        return 1

    project_root = Path(__file__).resolve().parent.parent
    spec_path = project_root / "linuxdoscanner-backend.spec"
    dist_path = project_root / "dist"
    work_path = project_root / "build" / "pyinstaller"

    PyInstaller.__main__.run(
        [
            str(spec_path),
            "--noconfirm",
            "--clean",
            "--distpath",
            str(dist_path),
            "--workpath",
            str(work_path),
        ]
    )

    exe_path = dist_path / "LinuxDoScannerBackend" / "LinuxDoScannerBackend.exe"
    print(f"Build complete: {exe_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
