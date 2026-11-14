from __future__ import annotations

import argparse
from pathlib import Path
import zipfile


PACKAGE_NAME = "Time_Stranger_Importer"
PACKAGE_ROOTS = ("__init__.py", "data", "external", "src", "docs", "README.md", "LICENSE", "LICENSE.txt")
REQUIRED_ROOTS = ("__init__.py", "data", "external", "src")
SKIP_DIRS = {"__pycache__", ".pytest_cache"}
SKIP_SUFFIXES = {".pyc", ".pyo"}


def _skip_path(path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.parts) or path.suffix.lower() in SKIP_SUFFIXES


def _iter_package_files(repo_root: Path):
    for root_name in REQUIRED_ROOTS:
        root = repo_root / root_name
        if not root.exists():
            raise SystemExit(f"missing addon package item: {root}")

    for root_name in PACKAGE_ROOTS:
        root = repo_root / root_name
        if not root.exists():
            continue

        if root.is_file():
            rel = root.relative_to(repo_root)
            if not _skip_path(rel):
                yield root, f"{PACKAGE_NAME}/{rel.as_posix()}"
            continue

        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(repo_root)
            if _skip_path(rel):
                continue
            yield path, f"{PACKAGE_NAME}/{rel.as_posix()}"


def build_zip(repo_root: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path, arcname in _iter_package_files(repo_root):
            zf.write(path, arcname)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build an installable Blender addon zip for Time Stranger Importer."
    )
    parser.add_argument(
        "--repo-root",
        default=Path(__file__).resolve().parents[1],
        type=Path,
        help="Repository root. Defaults to the parent of the tools folder.",
    )
    parser.add_argument(
        "--output",
        default=Path("dist") / f"{PACKAGE_NAME}.zip",
        type=Path,
        help="Output zip path.",
    )
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    output = args.output
    if not output.is_absolute():
        output = repo_root / output

    build_zip(repo_root, output)
    print(f"Wrote {output}")


if __name__ == "__main__":
    main()
