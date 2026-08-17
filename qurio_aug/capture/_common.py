"""Platform-independent pieces of the capture layer: types, exceptions,
window-matching logic shared by both backends, and the geometry that makes
ROI boxes survive different window sizes/aspect-ratio letterboxing across
platforms. See qurio_aug/capture/__init__.py for how this gets assembled
with a platform-specific backend (_macos.py or _windows.py).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PIL import Image


@dataclass(frozen=True)
class WindowInfo:
    window_id: int
    owner_name: str
    title: str
    bounds: tuple[float, float, float, float]  # x, y, width, height


class WindowNotFoundError(RuntimeError):
    pass


class AmbiguousWindowError(RuntimeError):
    def __init__(self, matches: list[WindowInfo]):
        self.matches = matches
        listing = "\n".join(f"  - {m.owner_name!r} / {m.title!r}" for m in matches)
        super().__init__(f"multiple windows matched:\n{listing}")


class ScreenCapturePermissionError(RuntimeError):
    """Raised when a capture came back blank. The likely cause (and fix)
    differs by platform, so each backend passes its own message rather
    than this carrying one fixed string -- callers only need the type.
    """

    def __init__(self, message: str | None = None):
        super().__init__(message or (
            "captured image was blank -- grant Screen Recording permission to "
            "the process running this script in System Settings > Privacy & "
            "Security > Screen Recording, then restart it"
        ))


def _title_matches(hint: str, owner: str, title: str) -> bool:
    hint = hint.lower()
    return hint in owner.lower() or hint in title.lower()


def find_game_window(
    find_windows_fn: Callable[[str], list[WindowInfo]], title_hint: str
) -> WindowInfo:
    """Shared zero/one/many-match validation, parameterized on the
    backend's own find_windows -- kept in exactly one place so a
    Windows-side divergence here can't hide a bug that macOS testing
    would never exercise.
    """
    matches = find_windows_fn(title_hint)
    if not matches:
        raise WindowNotFoundError(
            f"no on-screen window matching {title_hint!r} -- is the game running "
            "and its window visible (not minimized)?"
        )
    if len(matches) > 1:
        raise AmbiguousWindowError(matches)
    return matches[0]


# Only 16:9 is supported -- the automation's ROI boxes are meaningless
# against any other in-game aspect ratio, and the community this ships to
# doesn't need ultrawide support (confirmed with the user).
_ASPECT_16_9 = 16 / 9


def get_content_rect(width: int, height: int) -> tuple[int, int, int, int]:
    """The real 16:9 game content rectangle within a width x height
    capture, in pixels -- (x0, y0, x1, y1).

    Different platforms/window setups letterbox or pillarbox differently:
    on macOS, the CrossOver window this project was built against renders
    the game at a fixed 16:9 internally but the *window* itself isn't
    16:9, leaving black bars top and bottom. A Windows borderless-windowed
    capture is typically the content itself, no bars at all. Rather than
    pixel-scan for black bars (risking false positives against the game's
    own dark scenes -- the Smithy background has plenty of near-black
    pixels that aren't letterboxing), this is pure geometry: since only
    16:9 content is in scope, the content rect is always the largest
    centered 16:9 rectangle that fits inside (width, height). If the
    capture is already exactly 16:9, this is a no-op (content rect ==
    full image) -- so the same code path handles "no letterboxing at all"
    for free, not just the letterboxed case.

    Empirically validated against a real 2880x1800 macOS capture: this
    formula predicts y0=90 exactly; the measured first non-black pixel row
    in that real capture was also y=90 (see tests/test_capture_geometry.py
    for the pinned regression case).
    """
    if width / height > _ASPECT_16_9:  # wider than 16:9 -- pillarboxed
        content_h = height
        content_w = height * _ASPECT_16_9
        x_off, y_off = (width - content_w) / 2, 0
    else:  # 16:9 or taller -- letterboxed (or no bars at all if exactly 16:9)
        content_w = width
        content_h = width / _ASPECT_16_9
        x_off, y_off = 0, (height - content_h) / 2

    return (
        round(x_off),
        round(y_off),
        round(x_off + content_w),
        round(y_off + content_h),
    )


def crop_to_content(img: Image.Image) -> Image.Image:
    return img.crop(get_content_rect(*img.size))
