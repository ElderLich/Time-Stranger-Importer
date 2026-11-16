"""Set the Blender version in the release checkout or verify a manually pushed tag."""

import argparse
from pathlib import Path
import re


# The first push of the 1.5.6 series is run 1 of release.yml. Re-runs keep their number.
# For a new minor/major series, update bl_info and set this to its first push run number.
FIRST_SERIES_RUN = 1
VERSION_LINE = re.compile(r'^(    "version": )\((\d+),\s*(\d+),\s*(\d+)\)(,)$', re.MULTILINE)


def prepare(entry: Path, run_number: int | None, tag: str | None) -> str:
    source = entry.read_bytes().decode("utf-8")
    # Preserve the checkout's line endings when generating the release commit.
    match = VERSION_LINE.search(source.replace("\r\n", "\n"))
    if not match:
        raise ValueError("Cannot find the Blender version in bl_info")
    major, minor, patch = map(int, match.group(2, 3, 4))
    if run_number is not None:
        if run_number < FIRST_SERIES_RUN:
            raise ValueError("Release run precedes the configured version series")
        patch += run_number - FIRST_SERIES_RUN
        previous = match.group().strip()
        updated = f'"version": ({major}, {minor}, {patch}),'
        if previous != updated:
            entry.write_bytes(source.replace(previous, updated, 1).encode("utf-8"))
    version = f"v{major}.{minor}.{patch}"
    if tag is not None and tag != version:
        raise ValueError(f"Tag {tag} differs from Blender version {version}")
    return version


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--run-number", type=int)
    mode.add_argument("--tag")
    args = parser.parse_args()
    print(prepare(Path("__init__.py"), args.run_number, args.tag))
