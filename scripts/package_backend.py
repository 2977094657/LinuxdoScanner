from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path


def normalize_release_tag(tag_or_version: str) -> str:
    raw_value = str(tag_or_version or "").strip()
    if not raw_value:
        raise ValueError("Release tag must not be empty.")
    if raw_value.startswith("v"):
        return raw_value
    return f"v{raw_value}"


def package_backend(*, project_root: Path, tag_or_version: str, output_dir: Path) -> Path:
    release_tag = normalize_release_tag(tag_or_version)
    source_dir = project_root / "dist" / "LinuxDoScannerBackend"
    if not source_dir.exists():
        raise FileNotFoundError(f"Backend build directory not found: {source_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)
    archive_name = f"LinuxDoScannerBackend-{release_tag}-windows-x64.zip"
    archive_path = output_dir / archive_name
    if archive_path.exists():
        archive_path.unlink()

    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source_dir.rglob("*")):
            archive.write(path, arcname=Path("LinuxDoScannerBackend") / path.relative_to(source_dir))

    return archive_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Package the Windows backend build directory for release.")
    parser.add_argument("--tag", required=True, help="Git tag, for example v1.0.0")
    parser.add_argument("--output-dir", default="dist", help="Output directory, default: dist")
    args = parser.parse_args(argv)

    project_root = Path(__file__).resolve().parent.parent
    output_dir = (project_root / args.output_dir).resolve()

    try:
        archive_path = package_backend(
            project_root=project_root,
            tag_or_version=args.tag,
            output_dir=output_dir,
        )
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"Backend package complete: {archive_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
