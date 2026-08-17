# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for qurio-aug (main.py) and qurio-aug-calibrate
(calibrate.py), built as --onefile executables. Build with:

    pyinstaller packaging/qurio-aug.spec --noconfirm

after running this platform's packaging/vendor_tesseract_*.{sh,ps1}
script -- it must produce packaging/vendor/<platform>/tesseract/ before
this spec runs, or the build fails fast with a clear message rather than
silently shipping a binary with no OCR engine.

Two separate Analysis/EXE blocks rather than one shared build: calibrate's
crop-inspection output is the real diagnostic tool beta testers will need
if ROI reads look wrong on their setup, and --onefile mode has no shared
onedir folder to split the data payload across, so each exe just carries
its own copy.
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
# always catch. Harmless to list unconditionally, but only actually
# resolvable when building on Windows (where pywin32 is installed), so
# it's kept platform-gated rather than listed always.
HIDDEN_IMPORTS = ["win32timezone"] if sys.platform == "win32" else []


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
calibrate_exe = _onefile_exe(_analysis("calibrate.py"), "qurio-aug-calibrate")
