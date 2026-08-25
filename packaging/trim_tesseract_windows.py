"""Trims a raw Chocolatey/UB-Mannheim Tesseract install down to exactly
what tesseract.exe needs to run -- computed via a real PE import-table
dependency walk (BFS over DIRECTORY_ENTRY_IMPORT, starting at
tesseract.exe, following only edges that land on a DLL actually present
in the source directory), not guessed or hardcoded. Mirrors what
vendor_tesseract_macos.sh's dylibbundler already does automatically on
macOS.

Why this exists: vendor_tesseract_windows.ps1 used to just copy the
*entire* Chocolatey install (tesseract.exe plus a dozen model-training
tools this project never invokes -- text2image.exe, lstmtraining.exe,
mftraining.exe, etc. -- plus every DLL any of them might need, including
a large ICU/Pango/Cairo stack used only by text2image's rendering, not
OCR itself), specifically because there was no way to verify a trimmed
set actually still worked without real Windows hardware. Measured on
real Windows hardware (see CHANGELOG.md / docs/roadmap.md): of a ~229MB
raw install, ~110MB was dead weight (46MB of unused DLLs, ~64MB of
unused training executables) that a real dependency walk cleanly excludes.

Needs pefile, which is already a hard dependency of PyInstaller --
itself already a documented prerequisite for this whole packaging step
(see README's "Building the binary yourself").

Usage: python trim_tesseract_windows.py <source_dir> <dest_dir>
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pefile

ENTRY_EXE = "tesseract.exe"


def _direct_imports(pe_path: Path) -> set[str]:
    try:
        pe = pefile.PE(str(pe_path), fast_load=True)
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]])
    except Exception:
        return set()
    if not hasattr(pe, "DIRECTORY_ENTRY_IMPORT"):
        return set()
    return {entry.dll.decode("ascii", "ignore").lower() for entry in pe.DIRECTORY_ENTRY_IMPORT}


def compute_needed_files(source_dir: Path) -> dict[str, Path]:
    """Returns {lowercased filename: real on-disk path} for tesseract.exe
    itself plus its full transitive DLL dependency closure. Anything in
    source_dir not in this set (other .exe tools, unused DLLs, docs) is
    dead weight this project's code never touches.
    """
    available_dlls = {p.name.lower(): p for p in source_dir.glob("*.dll")}
    entry_path = source_dir / ENTRY_EXE
    if not entry_path.exists():
        raise FileNotFoundError(f"{entry_path} not found -- is source_dir a real Tesseract install?")

    needed: dict[str, Path] = {ENTRY_EXE.lower(): entry_path}
    frontier = [entry_path]
    visited: set[str] = set()
    while frontier:
        path = frontier.pop()
        key = path.name.lower()
        if key in visited:
            continue
        visited.add(key)
        for dep in _direct_imports(path):
            if dep in available_dlls and dep not in needed:
                dep_path = available_dlls[dep]
                needed[dep] = dep_path
                frontier.append(dep_path)
    return needed


def main() -> None:
    if len(sys.argv) != 3:
        print("usage: trim_tesseract_windows.py <source_dir> <dest_dir>", file=sys.stderr)
        sys.exit(1)
    source_dir, dest_dir = Path(sys.argv[1]), Path(sys.argv[2])

    needed = compute_needed_files(source_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    total_bytes = 0
    for path in needed.values():
        shutil.copy2(path, dest_dir / path.name)
        total_bytes += path.stat().st_size

    print(f"trimmed vendor: kept {len(needed)} files ({total_bytes / 1e6:.1f}MB) "
          f"of {ENTRY_EXE}'s real dependency closure, out of "
          f"{sum(1 for _ in source_dir.glob('*')) } total files in {source_dir}")


if __name__ == "__main__":
    main()
