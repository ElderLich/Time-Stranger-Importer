"""Load geometry extensions matching the running Blender Python ABI/platform.

See docs/architecture.md for building another backend. The bundled Windows x64
CPython 3.13 extension is verified in Blender 5.1.2 and 5.3.0.
"""

from __future__ import annotations

import sys
from pathlib import Path

import bpy


def load_format_modules():
    """Load modules that depend on the compiled ``dsts_formats`` backend.

    Raises RuntimeError if no .pyd matching the current Python version is found.
    """
    import importlib.machinery
    src_dir = Path(__file__).parent
    py_tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    # CPython owns platform/ABI suffix matching. Do not assume every Blender 5.x
    # embeds the same Python release or that a similarly named DLL can load.
    matching = [src_dir / ("dsts_formats" + suffix)
                for suffix in importlib.machinery.EXTENSION_SUFFIXES
                if (src_dir / ("dsts_formats" + suffix)).is_file()]
    if not matching:
        available = sorted(p.name for p in src_dir.glob("dsts_formats*") if p.is_file())
        raise RuntimeError(
            f"Blender {bpy.app.version_string} embeds Python {sys.version_info.major}.{sys.version_info.minor}. "
            f"No compatible {py_tag} dsts_formats extension is installed. "
            f"Available: {', '.join(available) or 'none'}. "
            "Install a backend built for this Python ABI and platform; see docs/architecture.md."
        )

    try:
        from . import dsts_formats, material, mesh, skeleton, utils
    except ImportError as exc:
        raise RuntimeError(
            f"Found {matching[0].name} but it failed to load: {exc}. "
            "Rebuild the backend for Blender's embedded Python."
        ) from exc

    return dsts_formats, material, mesh, skeleton, utils
