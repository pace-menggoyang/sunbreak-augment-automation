"""Public capture API: find the game window and screenshot it, on either
macOS or Windows. Dispatches to a platform-specific backend (_macos.py /
_windows.py) at import time based on sys.platform, so only the relevant
backend's platform-only dependencies (Quartz / pywin32) ever get imported
-- no try/except ImportError gymnastics needed inside either backend file.

screenshot_window() applies the 16:9 content-rect crop (see _common.py's
get_content_rect) centrally, once, here -- so both backends only need to
implement a "raw" screenshot of the window's own bounds, and every caller
(ocr.py, calibrate.py, state_machine.py) always gets back an
already-trimmed 16:9 image regardless of platform or window letterboxing.
"""
from __future__ import annotations

import sys

from PIL import Image

from qurio_aug.capture._common import (
    AmbiguousWindowError,
    ScreenCapturePermissionError,
    WindowInfo,
    WindowNotFoundError,
    crop_to_content,
    get_content_rect,
)
from qurio_aug.capture._common import find_game_window as _find_game_window

if sys.platform == "darwin":
    from qurio_aug.capture import _macos as _backend
elif sys.platform == "win32":
    from qurio_aug.capture import _windows as _backend
else:
    raise ImportError(
        f"unsupported platform: {sys.platform!r} -- only macOS and Windows are supported"
    )

__all__ = [
    "WindowInfo",
    "WindowNotFoundError",
    "AmbiguousWindowError",
    "ScreenCapturePermissionError",
    "get_content_rect",
    "find_windows",
    "find_game_window",
    "screenshot_window",
]

find_windows = _backend.find_windows


def find_game_window(title_hint: str) -> WindowInfo:
    return _find_game_window(find_windows, title_hint)


def screenshot_window(window: WindowInfo) -> Image.Image:
    return crop_to_content(_backend.screenshot_window_raw(window))
