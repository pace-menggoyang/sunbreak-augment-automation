"""Offline tests for support_bundle.build_support_bundle -- uses a real
temp directory with synthetic files (mtimes set explicitly via os.utime
for deterministic "newest" selection, rather than relying on sleep()
between creations) so no real logs/ contents or a live run are needed.
"""
import sys
import tempfile
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qurio_aug.support_bundle import build_support_bundle


def _touch(path: Path, content: str = "x", mtime: float | None = None) -> None:
    path.write_text(content)
    if mtime is not None:
        import os
        os.utime(path, (mtime, mtime))


def test_missing_log_dir_returns_none():
    with tempfile.TemporaryDirectory() as d:
        missing = Path(d) / "does-not-exist"
        assert build_support_bundle(missing) is None


def test_empty_log_dir_returns_none():
    with tempfile.TemporaryDirectory() as d:
        assert build_support_bundle(Path(d)) is None


def test_bundles_debug_log_and_its_siblings():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        _touch(d / "mygoal-20260101-120000.debug.log")
        _touch(d / "mygoal-20260101-120000.log")
        _touch(d / "mygoal-20260101-120000.jsonl")
        bundle = build_support_bundle(d)
        assert bundle is not None
        with zipfile.ZipFile(bundle) as zf:
            names = set(zf.namelist())
        assert names == {
            "mygoal-20260101-120000.debug.log",
            "mygoal-20260101-120000.log",
            "mygoal-20260101-120000.jsonl",
        }


def test_picks_the_newest_debug_log_not_an_older_one():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        now = time.time()
        _touch(d / "old-goal-20260101-090000.debug.log", mtime=now - 1000)
        _touch(d / "new-goal-20260101-120000.debug.log", mtime=now)
        bundle = build_support_bundle(d)
        with zipfile.ZipFile(bundle) as zf:
            names = zf.namelist()
        assert names == ["new-goal-20260101-120000.debug.log"]


def test_bundles_newest_failure_screenshot_cluster_only():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        now = time.time()
        _touch(d / "unreadable_20260101-090000-000_full.png", mtime=now - 1000)
        _touch(d / "unreadable_20260101-090000-000_row0_name.png", mtime=now - 1000)
        _touch(d / "unreadable_20260101-120000-500_full.png", mtime=now)
        _touch(d / "unreadable_20260101-120000-500_row0_value.png", mtime=now)
        bundle = build_support_bundle(d)
        with zipfile.ZipFile(bundle) as zf:
            names = set(zf.namelist())
        assert names == {
            "unreadable_20260101-120000-500_full.png",
            "unreadable_20260101-120000-500_row0_value.png",
        }


def test_bundles_both_debug_log_and_failure_screenshots_together():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        _touch(d / "mygoal-20260101-120000.debug.log")
        _touch(d / "unreadable_20260101-120500-000_full.png")
        bundle = build_support_bundle(d)
        with zipfile.ZipFile(bundle) as zf:
            names = set(zf.namelist())
        assert names == {
            "mygoal-20260101-120000.debug.log",
            "unreadable_20260101-120500-000_full.png",
        }


def test_ignores_unrelated_files_like_calibration_crops():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        _touch(d / "calibration_full.png")
        _touch(d / "calibration_row0_name.png")
        assert build_support_bundle(d) is None


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
