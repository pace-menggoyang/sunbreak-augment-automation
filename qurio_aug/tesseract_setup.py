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
