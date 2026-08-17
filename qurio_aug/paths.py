"""Resolves read-only bundled resource paths correctly whether running
from source or from a frozen PyInstaller build.

The `Path(__file__).resolve().parent.parent / ...` pattern used elsewhere
in this project points at the wrong place once frozen: PyInstaller
extracts bundled data to a temp directory referenced by sys._MEIPASS,
unrelated to where qurio_aug/*.py's own __file__ ends up inside the
onefile bundle. resource_dir() is the one place that needs to know about
that difference -- every other module just calls it.
"""
from __future__ import annotations

import sys
from pathlib import Path


def resource_dir(*parts: str) -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS, *parts)
    return Path(__file__).resolve().parent.parent.joinpath(*parts)
