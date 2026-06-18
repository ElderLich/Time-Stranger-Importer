"""Backend loading helpers for the compiled DSTS format module.

The compiled ``dsts_formats`` backend is a pybind11 C++ extension that must
match Blender's embedded Python version exactly (e.g. cp313 for Blender 5.1).

To rebuild it:
  1. Install VS2022 with the "Desktop development with C++" workload
  2. Install Python 3.13 (matching Blender 5.1) with dev headers
  3. pip install pybind11
  4. cd dsts-formats-parser && cmake -B build && cmake --build build --config Release
  5. Copy the resulting dsts_formats.cp313-win_amd64.pyd into this (src/) folder

This reset package requires a matching .pyd. The earlier pure-Python custom
reader/writer fallback was removed with the experimental custom-export tools.
"""

from __future__ import annotations

import sys
from pathlib import Path

import bpy


def load_format_modules():
    """Load modules that depend on the compiled ``dsts_formats`` backend.

    Raises RuntimeError if no .pyd matching the current Python version is found.
    """
    src_dir = Path(__file__).parent
    py_tag = f"cp{sys.version_info.major}{sys.version_info.minor}"

    available = sorted(src_dir.glob("dsts_formats*.pyd"))
    if not available:
        raise RuntimeError(
            f"No compiled dsts_formats backend found in addon src/ folder. "
            f"Blender {bpy.app.version_string} needs a {py_tag}-win_amd64.pyd. "
            "See src/backend.py for build instructions."
        )

    matching = [p for p in available if py_tag in p.name]
    if not matching:
        found_tags = ", ".join(p.name for p in available)
        raise RuntimeError(
            f"Compiled backend version mismatch: Blender {bpy.app.version_string} "
            f"needs {py_tag}-win_amd64.pyd but found: {found_tags}. "
            "See src/backend.py for build instructions."
        )

    try:
        from . import dsts_formats, material, mesh, skeleton, utils
    except ImportError as exc:
        raise RuntimeError(
            f"Found {matching[0].name} but it failed to load: {exc}. "
            "Rebuild the backend for Blender's embedded Python."
        ) from exc

    return dsts_formats, material, mesh, skeleton, utils
