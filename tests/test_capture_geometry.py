"""Offline tests for the platform-independent pieces of the capture layer
(qurio_aug/capture/_common.py) -- no Quartz or pywin32 needed, so these
run identically on macOS, Windows, and CI for either.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qurio_aug.capture._common import get_content_rect, _title_matches


def test_exact_16_9_is_a_no_op():
    # No letterboxing/pillarboxing at all -- content rect is the full image.
    assert get_content_rect(1920, 1080) == (0, 0, 1920, 1080)


def test_letterboxed_window_trims_top_and_bottom():
    # Window taller (relative to its width) than 16:9 -- bars top/bottom.
    # This is today's macOS/CrossOver case: the game renders 16:9 inside a
    # window that isn't 16:9 itself.
    x0, y0, x1, y1 = get_content_rect(1920, 1200)
    assert (x0, x1) == (0, 1920)  # no horizontal trim
    assert y0 > 0 and y1 < 1200  # trimmed top and bottom
    assert y1 - y0 == round(1920 * 9 / 16)


def test_letterboxed_window_matches_real_captured_frame():
    # Empirical regression case: measured directly against a real capture
    # from this project's live testing. The formula predicted y0=90; the
    # actual first non-black pixel row in that same 2880x1800 capture was
    # independently measured (by scanning real pixel brightness) at
    # y=90 exactly. Pinning this locks the formula against future drift,
    # not just a sanity check of the math in isolation.
    x0, y0, x1, y1 = get_content_rect(2880, 1800)
    assert (x0, y0, x1) == (0, 90, 2880)
    assert y1 == 1800 - 90


def test_pillarboxed_window_trims_left_and_right():
    # Window wider (relative to its height) than 16:9 -- bars left/right.
    # Not currently observed live, but the geometry is symmetric with the
    # letterboxed case and should be exercised the same way.
    x0, y0, x1, y1 = get_content_rect(2560, 1080)
    assert (y0, y1) == (0, 1080)  # no vertical trim
    assert x0 > 0 and x1 < 2560  # trimmed left and right
    assert x1 - x0 == round(1080 * 16 / 9)


def test_content_rect_never_exceeds_source_bounds():
    # Rounding shouldn't ever push the computed rect outside the actual
    # captured image, regardless of how the dimensions divide.
    for w, h in [(1921, 1081), (1919, 1079), (2870, 1800), (3413, 1440)]:
        x0, y0, x1, y1 = get_content_rect(w, h)
        assert 0 <= x0 <= x1 <= w
        assert 0 <= y0 <= y1 <= h


def test_title_matches_is_case_insensitive_substring_on_either_field():
    assert _title_matches("monster hunter", "MonsterHunterRise.exe", "Monster Hunter Rise")
    assert _title_matches("MONSTER HUNTER", "monsterhunterrise.exe", "monster hunter rise")


def test_title_matches_checks_owner_or_title_independently():
    assert _title_matches("crossover", "CrossOver", "Monster Hunter Rise")
    assert _title_matches("monster hunter", "CrossOver", "Monster Hunter Rise")


def test_title_matches_false_when_neither_field_matches():
    assert not _title_matches("monster hunter", "Finder", "Desktop")


def run_all():
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    run_all()
