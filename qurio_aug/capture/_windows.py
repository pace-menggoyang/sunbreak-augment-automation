"""Locate the game window on Windows and grab its pixels.

Written without any way to test it on real Windows hardware -- early
community members are the first real testers (see the beta checklist in
docs/). Kept as structurally close to _macos.py as possible, and as small
as possible, to minimize the surface for an untested bug to hide in.

Assumes **borderless windowed** mode, not exclusive fullscreen (confirmed
with the user as the community's common setup) -- a window-rect + screen-
region capture is sufficient here; true exclusive fullscreen can make a
window's own pixels invisible to APIs like this one (they'd need the
Windows Graphics Capture API instead, out of scope).

DPI awareness matters more than it looks like it should: without calling
SetProcessDpiAwareness below, win32gui.GetWindowRect() and mss's screen
grab can disagree about coordinate space on a scaled display (100%+
scaling is the common case on Windows laptops, not an edge case) --
every ROI box would then land a consistent-but-wrong distance off,
which looks like "mostly works, occasional misreads" rather than a clean
failure. This call has to happen before any window/screen coordinate is
read, hence at import time, before find_windows or screenshot_window_raw
can be called.

Only ever imported when sys.platform == "win32" (see
qurio_aug/capture/__init__.py's dispatch) -- the imports below are
intentionally unguarded, since this file should never load on a platform
where pywin32/mss aren't available... except mss, which *is* cross-
platform and already a dependency either way.
"""
from __future__ import annotations

import ctypes

import mss
import win32gui
import win32process
from PIL import Image

from qurio_aug.capture._common import ScreenCapturePermissionError, WindowInfo, _title_matches

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
except (AttributeError, OSError):
    try:
        ctypes.windll.user32.SetProcessDPIAware()  # older Windows fallback
    except (AttributeError, OSError):
        pass  # best-effort -- capture may be off on a scaled display if both fail

_WINDOWS_CAPTURE_ERROR = (
    "captured image was blank -- is the window minimized, or running in "
    "exclusive fullscreen instead of borderless windowed (only borderless "
    "windowed is supported)? If the game is running as Administrator, try "
    "running this as Administrator too."
)

# One mss.mss() instance, created lazily and reused for every capture --
# not one per call. mss's own docs recommend this: on Windows,
# instantiating it sets up real GDI resources (a device context, a
# compatible bitmap) under the hood, so creating and tearing one down for
# every single screenshot (potentially thousands of times over a long
# run) is real, avoidable overhead on top of whatever risk repeated GDI
# allocation/deallocation carries under sustained load. No teardown is
# needed for the lifetime of this process -- it's released when the
# process exits.
_sct: mss.mss | None = None


def _screen_capture() -> mss.mss:
    global _sct
    if _sct is None:
        _sct = mss.mss()
    return _sct


def _owner_name(hwnd: int) -> str:
    """Best-effort owning-process name -- returns "" rather than raising
    if it can't be determined. Deliberately defensive: if the game runs
    elevated and this doesn't, OpenProcess can raise Access Denied even
    though the window's title is still perfectly readable -- degrading to
    title-only matching (see find_windows) is much better than crashing
    over a window we could otherwise have matched fine.
    """
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        handle = win32process.OpenProcess(0x0400 | 0x0010, False, pid)  # QUERY_INFORMATION | VM_READ
        try:
            return win32process.GetModuleFileNameEx(handle, 0).rsplit("\\", 1)[-1]
        finally:
            handle.Close()
    except Exception:
        return ""


def find_windows(title_hint: str) -> list[WindowInfo]:
    """All visible windows whose owning process name or title contains
    title_hint (case-insensitive). Returns possibly-empty list; caller
    decides how to handle zero or multiple matches.
    """
    matches: list[WindowInfo] = []

    def _callback(hwnd: int, _extra) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        owner = _owner_name(hwnd)
        if _title_matches(title_hint, owner, title):
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            matches.append(
                WindowInfo(
                    window_id=hwnd,
                    owner_name=owner,
                    title=title,
                    bounds=(left, top, right - left, bottom - top),
                )
            )
        return True

    win32gui.EnumWindows(_callback, None)
    return matches


def screenshot_window_raw(window: WindowInfo) -> Image.Image:
    """Grab the current pixels of the screen region `window` occupies.
    "Raw" -- not yet trimmed to the 16:9 content rect; that's applied
    centrally by qurio_aug.capture.screenshot_window.

    Unlike macOS's window-ID-based Quartz capture, this is a screen-region
    grab: it captures whatever is actually on top of that region on
    screen, not the target window specifically. If another window,
    overlay, or notification is drawn over the game during a run, this
    silently captures that instead -- there's no error, just wrong OCR
    input. No fix planned for this (would need the Windows Graphics
    Capture API); documented as a hard requirement instead: keep the game
    window unobstructed for the duration of a run.
    """
    x, y, w, h = window.bounds
    sct = _screen_capture()
    raw = sct.grab({"left": int(x), "top": int(y), "width": int(w), "height": int(h)})
    img = Image.frombytes("RGB", raw.size, raw.rgb)
    if img.getbbox() is None:
        raise ScreenCapturePermissionError(_WINDOWS_CAPTURE_ERROR)
    return img
