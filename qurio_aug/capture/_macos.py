"""Locate the CrossOver game window on macOS and grab its pixels.

Uses Quartz's window-server APIs directly (not mss) so capture works by
window ID rather than screen coordinates -- robust to the window being
moved, resized, or partially covered by another window, as long as it's
not fully minimized.

Requires the process running this to have the macOS "Screen Recording"
permission (System Settings > Privacy & Security > Screen Recording),
otherwise captures come back black -- screenshot_window_raw checks for
that and raises a clear error rather than silently returning junk.

Only ever imported when sys.platform == "darwin" (see
qurio_aug/capture/__init__.py's dispatch) -- the `import Quartz` at module
level is intentionally unguarded, since this file should never load on a
platform where it isn't available.
"""
from __future__ import annotations

import Quartz
from PIL import Image

from qurio_aug.capture._common import ScreenCapturePermissionError, WindowInfo, _title_matches


def find_windows(title_hint: str) -> list[WindowInfo]:
    """All on-screen windows whose owner or title contains title_hint
    (case-insensitive). Returns possibly-empty list; caller decides how to
    handle zero or multiple matches.
    """
    window_list = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID
    )
    matches = []
    for w in window_list:
        owner = w.get("kCGWindowOwnerName", "") or ""
        title = w.get("kCGWindowName", "") or ""
        if _title_matches(title_hint, owner, title):
            b = w["kCGWindowBounds"]
            matches.append(
                WindowInfo(
                    window_id=w["kCGWindowNumber"],
                    owner_name=owner,
                    title=title,
                    bounds=(b["X"], b["Y"], b["Width"], b["Height"]),
                )
            )
    return matches


def _cgimage_to_pil(cgimage) -> Image.Image:
    width = Quartz.CGImageGetWidth(cgimage)
    height = Quartz.CGImageGetHeight(cgimage)
    bytes_per_row = Quartz.CGImageGetBytesPerRow(cgimage)
    provider = Quartz.CGImageGetDataProvider(cgimage)
    data = Quartz.CGDataProviderCopyData(provider)
    # bytes(data) leaks: calling the bytes() constructor on this
    # PyObjC-bridged CFData object leaks the full backing buffer every
    # single call (confirmed directly -- an isolated loop of just
    # CGWindowListCreateImage + CGDataProviderCopyData + bytes(data) grew
    # current RSS by ~20MB/call, one whole frame, with gc.collect() run
    # after every iteration; switching only this line to bytearray(data)
    # made the same loop completely flat over 30 iterations). This is the
    # real cause of a multi-GB memory climb over a long run -- every
    # single screenshot on macOS went through this path. bytearray(data)
    # (or memoryview(data).tobytes(), equally clean) uses the buffer
    # protocol instead of whatever bytes()'s constructor does
    # differently for a PyObjC-bridged buffer object.
    buf = bytearray(data)
    img = Image.frombuffer(
        "RGBA", (width, height), buf, "raw", "BGRA", bytes_per_row, 1
    )
    return img.convert("RGB")


def screenshot_window_raw(window: WindowInfo) -> Image.Image:
    """Grab the current pixels of `window` by window ID, regardless of
    focus or partial occlusion by other windows. "Raw" -- not yet trimmed
    to the 16:9 content rect; qurio_aug.capture.screenshot_window does
    that centrally so it isn't duplicated per backend.
    """
    cgimage = Quartz.CGWindowListCreateImage(
        Quartz.CGRectNull,  # null rect => use the target window's own bounds
        Quartz.kCGWindowListOptionIncludingWindow,
        window.window_id,
        Quartz.kCGWindowImageBoundsIgnoreFraming | Quartz.kCGWindowImageBestResolution,
    )
    if cgimage is None:
        raise ScreenCapturePermissionError()

    img = _cgimage_to_pil(cgimage)
    if img.getbbox() is None:  # fully black/blank image
        raise ScreenCapturePermissionError()
    return img
