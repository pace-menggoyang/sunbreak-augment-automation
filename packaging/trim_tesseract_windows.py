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

On top of that, tesseract.exe and libtesseract-5.dll themselves (both
the official UB-Mannheim build and Chocolatey's identical repackaging of
it -- confirmed byte-for-byte identical on real hardware) ship as
*unstripped* mingw builds: ~97MB of libtesseract-5.dll's 101MB is
orphaned DWARF debug sections and a COFF symbol table, useful to a
debugger and nothing else -- confirmed via a real PE section dump (the
long-name "/N" sections after .reloc), all flagged
IMAGE_SCN_MEM_DISCARDABLE (the Windows loader never keeps them resident
even in the unstripped original) and contiguous at the file's tail with
nothing after them but the COFF symbol/string table. _strip_debug_sections
below removes exactly that tail and fixes up the handful of header
fields that reference it (NumberOfSections, PointerToSymbolTable,
NumberOfSymbols, SizeOfImage) -- verified byte-identical OCR output
against the original across 5 real fixtures x 4 psm modes before this
was trusted enough to ship. No `strip`/`objcopy` needed (neither
Chocolatey nor this project's Python toolchain has one on Windows by
default); this is a self-contained, from-scratch reimplementation of
exactly the one thing this codebase needs stripped.

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


def _strip_debug_sections(path: Path) -> int:
    """Truncates off a PE file's trailing debug sections (mingw's
    convention: long-name "/N" COFF sections holding DWARF debug info,
    always emitted after every real section) plus the COFF symbol/string
    table that follows them, in place. Returns bytes saved (0 if this
    file has nothing to strip, or its layout doesn't match the one
    invariant this depends on -- see below -- in which case the file is
    left completely untouched rather than risking a subtly-corrupted
    binary).

    Safety invariant checked before touching anything: the debug
    sections must be an unbroken *suffix* of the section table in their
    on-disk order (matches every real file measured so far -- mingw
    always appends them last). If any kept section appears after a
    debug section in table order, this bails out doing nothing, since
    the "just lower NumberOfSections" trick below only works when the
    dropped entries are exactly the trailing ones.
    """
    try:
        pe = pefile.PE(str(path))
    except pefile.PEFormatError:
        return 0  # not a PE file (e.g. tessdata) -- nothing to strip

    sections = pe.sections
    is_debug = [s.Name.rstrip(b"\x00").startswith(b"/") for s in sections]
    if not any(is_debug):
        return 0
    first_debug = is_debug.index(True)
    if not all(is_debug[first_debug:]):
        return 0  # a kept section follows a debug one -- layout doesn't match the assumed invariant

    kept = sections[:first_debug]
    debug = sections[first_debug:]
    truncate_at = min(s.PointerToRawData for s in debug)
    # Every debug section is IMAGE_SCN_MEM_DISCARDABLE (confirmed on the
    # real files this was built against) -- the loader never keeps them
    # resident even today, so shrinking SizeOfImage to just the kept
    # sections' virtual range changes nothing about runtime behavior.
    image_end = max(s.VirtualAddress + s.Misc_VirtualSize for s in kept)
    alignment = pe.OPTIONAL_HEADER.SectionAlignment
    new_size_of_image = -(-image_end // alignment) * alignment

    original_size = path.stat().st_size
    pe.FILE_HEADER.NumberOfSections = len(kept)
    pe.FILE_HEADER.PointerToSymbolTable = 0
    pe.FILE_HEADER.NumberOfSymbols = 0
    pe.OPTIONAL_HEADER.SizeOfImage = new_size_of_image
    data = pe.write()[:truncate_at]
    pe.close()

    # Re-parse the stripped bytes before trusting them -- a parse
    # failure here means don't ship a maybe-corrupted file, keep the
    # original instead.
    try:
        pefile.PE(data=data)
    except pefile.PEFormatError:
        return 0

    path.write_bytes(data)
    return original_size - len(data)


def main() -> None:
    if len(sys.argv) != 3:
        print("usage: trim_tesseract_windows.py <source_dir> <dest_dir>", file=sys.stderr)
        sys.exit(1)
    source_dir, dest_dir = Path(sys.argv[1]), Path(sys.argv[2])

    needed = compute_needed_files(source_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    total_bytes = 0
    stripped_bytes = 0
    for path in needed.values():
        dest_path = dest_dir / path.name
        shutil.copy2(path, dest_path)
        if dest_path.suffix.lower() in (".dll", ".exe"):
            stripped_bytes += _strip_debug_sections(dest_path)
        total_bytes += dest_path.stat().st_size

    print(f"trimmed vendor: kept {len(needed)} files ({total_bytes / 1e6:.1f}MB) "
          f"of {ENTRY_EXE}'s real dependency closure, out of "
          f"{sum(1 for _ in source_dir.glob('*')) } total files in {source_dir}")
    if stripped_bytes:
        print(f"stripped {stripped_bytes / 1e6:.1f}MB of debug sections "
              f"(mingw builds ship these unstripped) -- {total_bytes / 1e6:.1f}MB final")


if __name__ == "__main__":
    main()
