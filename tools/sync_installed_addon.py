"""Verify and install a packaged add-on, keeping the previous installation.

Only explicitly selected Blender versions are changed. Run without --apply to
review the plan. No preferences, other add-ons or open .blend files are touched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
import uuid
import zipfile


PACKAGE = "Time_Stranger_Importer"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_package(path):
    files = {}
    folded = set()
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            parts = PurePosixPath(info.filename).parts
            if (len(parts) < 2 or parts[0] != PACKAGE or
                    any(p in (".", "..") or ":" in p or "\\" in p for p in parts) or
                    info.filename.startswith("/") or (info.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError(f"Unsafe or unexpected ZIP entry: {info.filename}")
            relative = "/".join(parts[1:])
            if relative.casefold() in folded:
                raise ValueError(f"Duplicate ZIP path: {relative}")
            folded.add(relative.casefold())
            files[relative] = archive.read(info)
    if "__init__.py" not in files or not any(n.startswith("src/dsts_formats") and n.endswith(".pyd") for n in files):
        raise ValueError("ZIP lacks the Importer entry point or Windows native backend")
    return files


def hashes(root):
    # pathlib's recursive glob may suppress a directory permission error.
    # An unreadable installation must never be mistaken for an empty one.
    result = {}
    def scan(folder):
        with os.scandir(folder) as entries:
            for entry in sorted(entries, key=lambda item: item.name):
                path = Path(entry.path)
                if entry.name == '__pycache__':
                    continue
                if entry.is_symlink() or getattr(path, 'is_junction', lambda: False)():
                    raise ValueError(f'Refusing redirected add-on content: {path}')
                if entry.is_dir(follow_symlinks=False):
                    scan(path)
                elif entry.is_file(follow_symlinks=False) and path.suffix not in ('.pyc', '.pyo'):
                    result[path.relative_to(root).as_posix()] = digest(path.read_bytes())
    scan(root)
    return result


def sync(zip_path, blender_root, versions, apply=False):
    zip_path, blender_root = Path(zip_path).resolve(), Path(blender_root).resolve()
    files = read_package(zip_path)
    expected = {name: digest(data) for name, data in files.items()}
    plans = []
    for version in versions:
        if version not in ("5.1", "5.3"):
            raise ValueError(f"Unverified Blender version: {version}")
        scripts = blender_root / version / "scripts"
        target = scripts / "addons" / PACKAGE
        if not target.is_dir():
            raise FileNotFoundError(f"Existing add-on installation required: {target}")
        if target.is_symlink() or target.resolve() != target:
            raise ValueError(f"Refusing redirected add-on folder: {target}")
        before = hashes(target)
        plans.append({"version": version, "target": str(target), "before": before,
                      "changed": [n for n, h in expected.items() if before.get(n) != h],
                      "retired": sorted(set(before) - set(expected))})
    report = {"zip": str(zip_path), "zip_sha256": digest(zip_path.read_bytes()),
              "files": len(files), "applied": False, "installations": []}
    completed = []
    try:
        for plan in plans:
            target = Path(plan["target"])
            result = {k: v for k, v in plan.items() if k != "before"}
            report["installations"].append(result)
            if not apply or not plan["changed"] and not plan["retired"]:
                result["verified"] = hashes(target) == expected
                continue
            if hashes(target) != plan["before"]:
                raise RuntimeError(f"Installation changed after planning: {target}")
            # Stage on the same volume, outside Blender's add-on discovery root.
            backups = target.parent.parent / ".codex-addon-backups"
            backups.mkdir(exist_ok=True)
            backup = backups / (PACKAGE + "-" + uuid.uuid4().hex)
            with tempfile.TemporaryDirectory(prefix=".dsts-stage-", dir=backups) as tmp:
                staged = Path(tmp) / PACKAGE
                staged.mkdir()
                for relative, data in files.items():
                    path = staged / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
                if hashes(staged) != expected:
                    raise OSError("Staged add-on hash verification failed")
                os.rename(target, backup)
                try:
                    os.rename(staged, target)
                except BaseException:
                    os.rename(backup, target)
                    raise
                completed.append((target, backup))
                if hashes(target) != expected:
                    raise OSError(f"Installed add-on hash verification failed: {target}")
            result.update(backup=str(backup), verified=True)
        report["applied"] = apply
        return report
    except BaseException:
        for target, backup in reversed(completed):
            # Keep the failed new version too; never recursively delete an installation.
            failed = backup.with_name(backup.name + "-failed-new")
            os.rename(target, failed)
            os.rename(backup, target)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", required=True, type=Path)
    parser.add_argument("--blender-root", required=True, type=Path)
    parser.add_argument("--versions", nargs="+", default=["5.1", "5.3"])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = sync(args.zip, args.blender_root, args.versions, args.apply)
    output = json.dumps(report, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(output, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
