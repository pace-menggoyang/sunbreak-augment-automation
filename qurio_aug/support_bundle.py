"""Bundles the most recent debug log (+ matching .log/.jsonl) and the most
recent unreadable-roll failure screenshots from logs/ into one zip -- so
reporting a bug doesn't require knowing which files in logs/ are the
relevant ones, or attaching a dozen separate screenshots by hand.

"Last failure" here means two independently-found things, not one
correlated event: the newest *.debug.log (whatever run happened most
recently, success or not -- it's still the most relevant trace) and the
newest unreadable_*_full.png cluster (the most recent hard failure, which
may be from an earlier run than the newest debug log if the most recent
run itself succeeded). Bundling both, even when they're from different
runs, is still strictly more useful than guessing which one the reporter
actually means -- and if there's no unreadable_* cluster at all, that's
itself useful signal (no hard OCR failure occurred; whatever's being
reported is a different kind of problem).
"""
from __future__ import annotations

import time
import zipfile
from pathlib import Path

LOG_DIR = Path("logs")


def _newest(paths: list[Path]) -> Path | None:
    return max(paths, key=lambda p: p.stat().st_mtime, default=None)


def build_support_bundle(log_dir: Path = LOG_DIR) -> Path | None:
    """Writes logs/support-bundle-<timestamp>.zip and returns its path, or
    None if there's nothing in log_dir worth bundling (no debug log and no
    failure screenshots -- e.g. logs/ doesn't exist yet, or only holds
    calibration crops).
    """
    if not log_dir.is_dir():
        return None

    files: list[Path] = []

    debug_log = _newest(list(log_dir.glob("*.debug.log")))
    if debug_log is not None:
        files.append(debug_log)
        stem = debug_log.name[: -len(".debug.log")]  # "<goal>-<timestamp>"
        for suffix in (".log", ".jsonl"):
            sibling = log_dir / f"{stem}{suffix}"
            if sibling.is_file():
                files.append(sibling)

    latest_failure = _newest(list(log_dir.glob("unreadable_*_full.png")))
    if latest_failure is not None:
        prefix = latest_failure.name[: -len("_full.png")]  # "unreadable_<timestamp>"
        files.extend(sorted(log_dir.glob(f"{prefix}_*.png")))

    if not files:
        return None

    out_path = log_dir / f"support-bundle-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(f, arcname=f.name)
    return out_path
