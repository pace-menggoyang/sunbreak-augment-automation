"""Points pytesseract at the bundled Tesseract binary when running as a
frozen PyInstaller build, and self-checks that it actually runs -- so a
bundling failure produces one clear line of output instead of an
inscrutable crash deep inside pytesseract on the first OCR call, which a
non-technical user would have no way to diagnose. See
packaging/vendor_tesseract_*.{sh,ps1} for how the binary + tessdata get
into resources/tesseract/ in the compiled build in the first place.

When running from source (not frozen), this is a no-op: pytesseract just
uses whatever `tesseract` it finds on PATH, exactly as before this module
existed (brew/apt/choco install, per README).
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytesseract


def _bundled_tesseract_dir() -> Path | None:
    if not getattr(sys, "frozen", False):
        return None
    d = Path(sys._MEIPASS, "resources", "tesseract")
    return d if d.exists() else None


def configure_tesseract() -> None:
    bundled = _bundled_tesseract_dir()
    if bundled is None:
        return
    exe = bundled / ("tesseract.exe" if sys.platform == "win32" else "tesseract")
    pytesseract.pytesseract.tesseract_cmd = str(exe)
    os.environ["TESSDATA_PREFIX"] = str(bundled / "tessdata")


def tesserocr_tessdata_dir() -> str | None:
    """Where tesserocr (see qurio_aug/ocr.py) should look for tessdata, or
    None if it can't be resolved. Unlike pytesseract's subprocess call --
    which just runs whatever `tesseract` a shell would find, and lets that
    binary resolve its own tessdata relative to its own install location --
    tesserocr's in-process API always needs an explicit path: the wheel it
    installs from (simonflueckiger/tesserocr-windows_build) bundles its own
    tesseract.dll/leptonica but no tessdata of its own.

    Frozen: the same resources/tesseract/tessdata/ already vendored for
    pytesseract's subprocess use (see packaging/vendor_tesseract_windows.ps1)
    -- tessdata is just data files, shared between both OCR paths for free.

    Source: derives it from wherever pytesseract.tesseract_cmd actually
    resolves (the default "tesseract" string, looked up on PATH, unless
    something's already pointed it elsewhere) and assumes the standard
    install layout (a tessdata/ sibling directory) -- true for choco,
    the UB-Mannheim installer, and Homebrew alike.
    """
    bundled = _bundled_tesseract_dir()
    if bundled is not None:
        tessdata = bundled / "tessdata"
        return str(tessdata) if tessdata.is_dir() else None

    cmd = pytesseract.pytesseract.tesseract_cmd
    exe = Path(cmd) if Path(cmd).is_absolute() else None
    if exe is None:
        found = shutil.which(cmd)
        exe = Path(found) if found else None
    if exe is None:
        return None
    tessdata = exe.resolve().parent / "tessdata"
    return str(tessdata) if tessdata.is_dir() else None


def selfcheck() -> str:
    """Returns the tesseract version string, or raises RuntimeError with a
    message written for a non-technical user to paste into a bug report.
    """
    configure_tesseract()
    try:
        version = pytesseract.get_tesseract_version()
    except Exception as e:
        raise RuntimeError(
            f"the OCR engine failed to start ({e}). This usually means "
            "antivirus quarantined a bundled file, or the download was "
            "corrupted. Try: re-download the release zip; check your "
            "antivirus quarantine list; on Windows, try running as "
            "Administrator once. If it still fails, report this exact "
            "message on the project's GitHub Issues page."
        ) from e
    return str(version)
