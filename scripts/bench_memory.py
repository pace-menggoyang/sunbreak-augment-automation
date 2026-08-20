"""Memory benchmark for the per-attempt read pipeline (state_machine.
read_full_roll), simulating N attempts against real reference captures
instead of the live game -- run this to get a precise, measured answer to
"does memory grow with attempt count" instead of guessing from a code
read.

Exercises exactly the code every real attempt runs for a single-page roll:
page-indicator detection (with its retry path) and read_page's threaded
OCR -- the surface touched by the 0.1.2-beta changes (the row thread
pool, is_augmentation_results_screen; mss reuse is Windows-only capture
code, out of reach here). GameInput is stubbed out (park_mouse is the
only method a single-page read_full_roll calls on it) so this runs on
any platform/machine, no game or window capture required.

Usage:
  .venv/bin/python scripts/bench_memory.py [attempts]

Reports tracemalloc's Python-level heap size (precise, GC'd before each
sample so it reflects live objects only, not transient garbage) and the
OS-level RSS high-water mark, sampled at checkpoints through the run, plus
a slope computed over the second half (skipping warmup) so growth is a
number, not a squint at a printout. If growth is found, prints the top
allocating lines via tracemalloc's snapshot diff so it's actionable
instead of just a yes/no.
"""
from __future__ import annotations

import gc
import sys
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image

from qurio_aug import ocr, state_machine
from qurio_aug.decision import Goal, Profile, RequiredSkill

REFS = ROOT / "step-references"
PAGE1_SINGLE = REFS / "step-4-augmentation-result.png"


class FakeGame:
    """Stands in for GameInput -- read_full_roll only ever calls
    park_mouse/next_page/prev_page on it, never presses real keys.
    Tracks current_page so the fake screenshot_fn can respond to Q/E
    the same way the real game would.
    """

    def __init__(self) -> None:
        self.current_page = 1

    def park_mouse(self, bounds) -> None:
        pass

    def next_page(self) -> None:
        self.current_page += 1

    def prev_page(self) -> None:
        self.current_page -= 1


def _make_rss_reader():
    """Cross-platform current-RSS reader (not a high-water mark -- this
    tracks live growth/shrinkage, which matters on Windows since a
    plateau there can't be told apart from a leak using a monotonic
    peak). POSIX: /proc or resource.getrusage's ru_maxrss (peak, but
    it's the only cheap number available without psutil, which isn't a
    project dependency). Windows: GetProcessMemoryInfo via ctypes,
    already how this project talks to Windows APIs elsewhere (see
    capture/_windows.py) -- no new dependency needed since pywin32 is
    already required on that platform, but this avoids importing it.
    """
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        psapi = ctypes.WinDLL("psapi")
        kernel32 = ctypes.WinDLL("kernel32")
        handle = kernel32.GetCurrentProcess()
        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)

        def read() -> float:
            psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
            return counters.WorkingSetSize / 1e6

        return read

    import resource

    kb_per_unit = 1 / 1024 if sys.platform == "darwin" else 1  # macOS: bytes; Linux: KB already

    def read() -> float:  # POSIX: ru_maxrss is a high-water mark, not current -- still shows growth if any
        peak_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * kb_per_unit
        return peak_kb / 1024

    return read


_rss_mb = _make_rss_reader()


def run_attempts(n: int, region_config: ocr.RegionConfig) -> None:
    goal = Goal(
        name="bench",
        augment_type="skills_plus",
        profiles=(Profile(required_skills=(RequiredSkill("placeholder", min_level=1),)),),
        protected_skills=frozenset(),
    )

    tracemalloc.start(15)  # keep 15 frames per traceback so the diff below can point at a real call site
    checkpoints: list[tuple[int, float, float, float]] = []  # attempt, current_mb, peak_mb, rss_mb
    first_snapshot = None
    t0 = time.monotonic()

    for i in range(1, n + 1):
        game = FakeGame()

        def screenshot_fn() -> Image.Image:
            return Image.open(PAGE1_SINGLE)

        state_machine.read_full_roll(
            screenshot_fn, game, region_config, window_bounds=(0, 0, 2870, 1800),
            goal=goal, settle_delay=0.0,
        )

        if i % 10 == 0 or i == n:
            gc.collect()
            current, peak = tracemalloc.get_traced_memory()
            checkpoints.append((i, current / 1e6, peak / 1e6, _rss_mb()))
            print(f"  attempt {i:>4}  py_current={current/1e6:7.2f}MB  py_peak={peak/1e6:7.2f}MB  rss_now={_rss_mb():7.1f}MB")
            if i == n // 2:
                first_snapshot = tracemalloc.take_snapshot()

    elapsed = time.monotonic() - t0
    last_snapshot = tracemalloc.take_snapshot()
    tracemalloc.stop()

    print(f"\n{n} attempts in {elapsed:.1f}s ({elapsed/n*1000:.0f}ms/attempt)")

    # Slope over the second half only -- skips warmup (first-call imports,
    # digit-template cache fill, thread-pool spin-up) so it isn't mistaken
    # for ongoing per-attempt growth.
    half = checkpoints[len(checkpoints) // 2:]
    if len(half) >= 2:
        (a0, c0, _, _), (a1, c1, _, _) = half[0], half[-1]
        slope_kb_per_attempt = (c1 - c0) * 1000 / (a1 - a0)
        print(f"steady-state slope: {slope_kb_per_attempt:+.2f} KB/attempt (measured attempts {a0}-{a1})")
        projected_100 = slope_kb_per_attempt * 100 / 1024
        if abs(slope_kb_per_attempt) < 5:
            print(f"verdict: FLAT -- no meaningful growth (~{projected_100:+.2f}MB projected over 100 attempts). Not a leak.")
        else:
            print(f"verdict: GROWING -- ~{projected_100:+.2f}MB projected over 100 attempts. Looks like a real leak.")
            print("\ntop growing allocation sites (second half of the run):")
            for stat in last_snapshot.compare_to(first_snapshot, "traceback")[:8]:
                print(f"  +{stat.size_diff/1024:8.1f} KB  (+{stat.count_diff:5d} objects)  {stat.traceback[-1]}")


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    if not PAGE1_SINGLE.is_file():
        print(f"missing reference capture: {PAGE1_SINGLE} (gitignored step-references/ -- run from a checkout that has it)")
        return

    region_config = ocr.load_region_config()

    print(f"=== {n} single-page attempts ===")
    run_attempts(n, region_config)


if __name__ == "__main__":
    main()
