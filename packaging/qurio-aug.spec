# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for qurio-aug (main.py), built as a --onefile
executable. Build with:

    pyinstaller packaging/qurio-aug.spec --noconfirm

after running this platform's packaging/vendor_tesseract_*.{sh,ps1}
script -- it must produce packaging/vendor/<platform>/tesseract/ before
this spec runs, or the build fails fast with a clear message rather than
silently shipping a binary with no OCR engine.

Used to also build a separate qurio-aug-calibrate exe from calibrate.py,
which --onefile mode has no shared onedir folder to split the data
payload across, so it carried its own entire duplicate copy of
everything (Python runtime, Tesseract, tesserocr) just to expose one
function (calibrate.main) that main.py's own --calibrate flag now
covers too -- roughly doubling the download for zero unique
functionality. Dropped once that redundancy was measured directly
against a real release build: qurio-aug-windows.zip was 91.6MB for two
~46MB exes, one of which added nothing calibrate.main() being callable
from qurio-aug.exe itself didn't already provide.
"""
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent

PLATFORM_DIR = "windows" if sys.platform == "win32" else "macos"
TESSERACT_VENDOR_DIR = ROOT / "packaging" / "vendor" / PLATFORM_DIR / "tesseract"

if not TESSERACT_VENDOR_DIR.exists():
    script = "vendor_tesseract_windows.ps1" if sys.platform == "win32" else "vendor_tesseract_macos.sh"
    raise SystemExit(f"{TESSERACT_VENDOR_DIR} not found -- run packaging/{script} first")

DATAS = [
    (str(ROOT / "data" / "skills.json"), "data"),
    (str(ROOT / "data" / "digit_templates"), "data/digit_templates"),
    (str(ROOT / "configs" / "regions.yaml"), "configs"),
    (str(ROOT / "configs" / "goals"), "configs/goals"),
    (str(TESSERACT_VENDOR_DIR), "resources/tesseract"),
]

# win32timezone is a common PyInstaller+pywin32 gap: pytz/pywin32's own
# timezone glue imports it lazily in a way the dependency scanner doesn't
# always catch. tesserocr.cysignals is the same class of gap for the
# tesserocr accelerator (see qurio_aug/ocr.py, docs/ocr-performance-
# research.md #1b): PyInstaller's static analysis doesn't trace it as a
# dependency even though it's a real nested compiled submodule tesserocr
# needs at import time -- confirmed live: omitting it produced a working
# build whose --selfcheck reported the accelerator silently inactive
# ("No module named 'tesserocr.cysignals'"), not a build failure, so this
# is easy to miss without actually running the compiled exe. Both are
# harmless to list unconditionally, but only actually resolvable when
# building on Windows (where pywin32/tesserocr are installed), so kept
# platform-gated rather than listed always.
HIDDEN_IMPORTS = ["win32timezone", "tesserocr.cysignals"] if sys.platform == "win32" else []


def _analysis(script: str) -> Analysis:
    return Analysis(
        [str(ROOT / "qurio_aug" / script)],
        pathex=[str(ROOT)],
        datas=DATAS,
        hiddenimports=HIDDEN_IMPORTS,
    )


def _onefile_exe(analysis: Analysis, name: str) -> EXE:
    pyz = PYZ(analysis.pure, analysis.zipped_data)
    return EXE(
        pyz,
        analysis.scripts,
        analysis.binaries,
        analysis.zipfiles,
        analysis.datas,
        [],
        name=name,
        console=True,
    )


main_exe = _onefile_exe(_analysis("main.py"), "qurio-aug")
