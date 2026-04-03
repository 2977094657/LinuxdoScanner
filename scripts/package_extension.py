from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path


SEMVER_PATTERN = re.compile(r"^v?(?P<version>\d+\.\d+\.\d+(?:\.\d+)?)$")


def normalize_release_tag(tag_or_version: str) -> tuple[str, str]:
    raw_value = str(tag_or_version or "").strip()
    match = SEMVER_PATTERN.fullmatch(raw_value)
    if not match:
        raise ValueError(
            "Release version must look like v1.0.0 or 1.0.0 and must satisfy Chrome extension version rules."
        )
    version = match.group("version")
    return f"v{version}", version


def package_extension(*, project_root: Path, tag_or_version: str, output_dir: Path) -> Path:
    release_tag, version = normalize_release_tag(tag_or_version)
    source_dir = project_root / "chrome-extension"
    if not source_dir.exists():
        raise FileNotFoundError(f"Extension directory not found: {source_dir}")

    manifest_path = source_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Extension manifest not found: {manifest_path}")

    output_dir.mkdir(parents=True, exist_ok=True)
    package_root_name = f"LinuxDoScannerExtension-{release_tag}"
    staged_dir = output_dir / package_root_name
    archive_path = output_dir / f"{package_root_name}.zip"

    if staged_dir.exists():
        shutil.rmtree(staged_dir)
    if archive_path.exists():
        archive_path.unlink()

    shutil.copytree(source_dir, staged_dir)

    manifest = json.loads((staged_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest["version"] = version
    (staged_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(staged_dir.rglob("*")):
            archive.write(path, arcname=Path(package_root_name) / path.relative_to(staged_dir))

    return archive_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Package the Chrome extension and update manifest version from the release tag.")
    parser.add_argument("--tag", required=True, help="Git tag, for example v1.0.0")
    parser.add_argument("--output-dir", default="dist", help="Output directory, default: dist")
    args = parser.parse_args(argv)

    project_root = Path(__file__).resolve().parent.parent
    output_dir = (project_root / args.output_dir).resolve()

    try:
        archive_path = package_extension(
            project_root=project_root,
            tag_or_version=args.tag,
            output_dir=output_dir,
        )
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"Extension package complete: {archive_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
