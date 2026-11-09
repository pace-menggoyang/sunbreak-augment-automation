"""Timing probe: measures how long each screen of the augment cycle takes to
appear after a key press, so delays can be chosen from data instead of
guessed.

Why this exists: two speed-ups in a row were built on delays nobody had
measured. The wait before reading a roll was cut from 0.5s to 0.2s and
every roll read as empty (the skill rows are drawn somewhere between ~0.38s
and ~0.68s after the last key -- that window is exactly what this tool
narrows down), and 0.12s between keys was reported as too fast where a modal
is about to appear.

What it does: from the Material Select screen, it runs one full cycle of the
bot's own key sequence (start a roll, reach the results, then reject it and
come back to Material Select). After EVERY key press it takes screenshots
back to back for a second or so and records, per frame:

- how different the frame still is from the step's final frame (when that
  stops changing, the screen has settled), and
- when each key region stopped changing: the results title, the
  Defense/Slots/Resistance rows, and the skill rows. The skill rows are drawn
  last, so the skill-row area settling is when they appeared -- the number
  the settle wait must exceed. (Judged by "stopped changing", not "has
  bright content": the game's constant "Skills" header sits inside the skill
  rows box, so mere presence would read as ready the moment the header
  appears.)
- whether the roll had any skills at all, from green/red/orange pixels in
  the value column only (clear of that header).

It also saves each step's final screenshot -- reference images for every
screen of the cycle, which a future "wait until the next screen is really
there" check needs.

It costs one real augment attempt per cycle (materials and zenny), the same
as a normal farming attempt; the roll is rejected, so the armor is left
unchanged. Everything that touches the game or the clock is injectable, so
the analysis is unit-tested offline with synthetic frames.
"""
from __future__ import annotations

import csv
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PIL import Image, ImageChops, ImageStat

from qurio_aug import ocr
from qurio_aug.hotkeys import StopRequested

LOG_DIR = Path("logs")

# (step name, key action, seconds to keep capturing after the press). The
# sequence is the bot's own (see input.GameInput): trigger_roll() then
# reroll_macro(), one cycle, ending back on Material Select. The step that
# shows the results is the one the settle wait is about.
PROBE_STEPS: tuple[tuple[str, str, float], ...] = (
    ("autoselect", "autoselect", 1.0),
    ("confirm_to_proceed_modal", "confirm", 1.0),
    ("confirm_to_results", "confirm", 1.8),
    ("cancel_to_keep_modal", "cancel", 1.0),
    ("left_in_keep_modal", "left", 1.0),
    ("confirm_to_continue_modal", "confirm", 1.0),
    ("confirm_to_material_select", "confirm", 1.0),
)
RESULTS_STEP = "confirm_to_results"

# Frames are compared at this size -- big enough to see a panel appear,
# small enough that comparing dozens of frames costs nothing.
_DIFF_SIZE = (96, 54)
# Regions are compared at this size when judging when they stopped changing.
_REGION_DIFF_SIZE = (48, 24)
# A roll "had skills" if the value column holds at least this many coloured
# (green gain / red loss / orange maxed-gain) pixels at the end of the step.
_MIN_SKILL_VALUE_PIXELS = 20
_COLOR_MARGIN = 30  # same dominance margin ocr.py uses for the digit colours
_TITLE_MIN_PIXELS = 20
# What the bot waits today between the start of the final key press and its
# first capture: key hold + post-press delay + settle (0.03 + 0.18 + 0.5).
CURRENT_READ_DELAY_MS = 710

Box = tuple[float, float, float, float]


@dataclass
class Frame:
    t: float  # seconds since the key press began
    gray: Image.Image  # whole frame, downscaled, for diffing
    crops: dict[str, Image.Image]  # each tracked region, downscaled, for diffing


@dataclass
class StepResult:
    name: str
    action: str
    frames: int
    interval_ms: float  # median time between frames
    settle_ms: float | None  # when the whole frame stopped changing
    region_settle_ms: dict[str, float | None]  # when each region stopped changing
    # Skill values were drawn at the end of the step. Only meaningful for the
    # results step: the Material Select screen's orange material values also
    # look like skill values to this colour test.
    had_skills: bool
    had_title: bool  # the results title was on screen at the end of the step
    noise: float  # frame-to-frame difference once settled (animated glow etc.)


@dataclass
class ProbeReport:
    steps: list[StepResult] = field(default_factory=list)
    csv_path: Path | None = None
    summary_path: Path | None = None
    images: list[Path] = field(default_factory=list)


# --- measuring a frame -------------------------------------------------------


def _row_boxes(config: ocr.RegionConfig) -> list:
    return [row for key in ("single_page", "first_of_multi") for row in config.row_templates[key]]


def region_boxes(config: ocr.RegionConfig) -> dict[str, Box]:
    """The regions whose settling is tracked, as fractions of the frame."""
    boxes = [box for row in _row_boxes(config) for box in (row.name_box, row.value_box)]
    skill_rows = (
        min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes),
    )
    return {
        "title": config.results_title_box,
        # Defense / Slots / Resistances sit above the skills in both layouts.
        "stat_rows": (skill_rows[0], 0.22, skill_rows[2], skill_rows[1]),
        "skill_rows": skill_rows,
        "dialog": config.dialog_box,
    }


def value_column_box(config: ocr.RegionConfig) -> Box:
    """The column the "Lv +1" values sit in, across both page layouts. It is
    to the right of, and below, the constant "Skills" header, so anything
    coloured in it is a drawn skill value."""
    values = [row.value_box for row in _row_boxes(config)]
    return (
        min(b[0] for b in values), min(b[1] for b in values), max(b[2] for b in values), max(b[3] for b in values),
    )


def _count_pixels(crop: Image.Image, test: Callable[[int, int, int], bool]) -> int:
    rgb = crop.convert("RGB")
    px = rgb.load()
    w, h = rgb.size
    return sum(1 for y in range(h) for x in range(w) if test(*px[x, y]))


def _is_skill_value_pixel(r: int, g: int, b: int) -> bool:
    gain = g > r + _COLOR_MARGIN and g > b + _COLOR_MARGIN  # green
    loss_or_max = r > g + _COLOR_MARGIN and r > b + _COLOR_MARGIN  # red / orange
    return (gain or loss_or_max) and max(r, g, b) > ocr.BRIGHT_THRESHOLD


def measure_frame(img: Image.Image, t: float, boxes: dict[str, Box]) -> Frame:
    """The cheap per-frame part: downscaled copies for diffing. Kept light
    on purpose -- it runs between screenshots, and every millisecond here is
    time resolution lost (colour counting, which is slower, is done once on
    the final frame only: see count_final_pixels)."""
    return Frame(
        t=t,
        gray=img.convert("L").resize(_DIFF_SIZE, Image.BILINEAR),
        crops={
            name: ocr._crop_fraction(img, box).convert("L").resize(_REGION_DIFF_SIZE, Image.BILINEAR)
            for name, box in boxes.items()
        },
    )


def count_final_pixels(img: Image.Image, boxes: dict[str, Box], value_box: Box) -> tuple[int, int]:
    """(skill-value pixels, title pixels) of a step's final frame."""
    skill_values = _count_pixels(ocr._crop_fraction(img, value_box), _is_skill_value_pixel)
    title = _count_pixels(
        ocr._crop_fraction(img, boxes["title"]), lambda r, g, b: max(r, g, b) > ocr.BRIGHT_THRESHOLD,
    )
    return skill_values, title


def _diff(a: Image.Image, b: Image.Image) -> float:
    return ImageStat.Stat(ImageChops.difference(a, b)).mean[0]


# --- analysing a step --------------------------------------------------------


def _first_stable_ms(frames: list[Frame], is_bad: Callable[[int], bool]) -> float | None:
    """Time of the first frame after the last 'bad' one -- i.e. when the
    condition became true and stayed true. None if it never settled."""
    last_bad = -1
    for i in range(len(frames)):
        if is_bad(i):
            last_bad = i
    idx = last_bad + 1
    return frames[idx].t * 1000 if idx < len(frames) else None


def _settle_ms(frames: list[Frame], diffs: list[float]) -> float | None:
    """When a sequence of diff-to-final values dropped to noise and stayed."""
    tail = diffs[max(0, len(diffs) - 4):-1] or [0.0]
    # Once settled the frame still flickers a little (the panel border glow is
    # a continuous animation), so "unchanged" means within a few times that
    # noise, not exactly zero.
    threshold = max(1.0, 2 * statistics.mean(tail))
    return _first_stable_ms(frames, lambda i: diffs[i] > threshold)


def analyze_step(
    name: str, action: str, frames: list[Frame], skill_value_pixels: int = 0, title_pixels: int = 0,
) -> StepResult:
    """Turns one step's frames into when-did-it-settle numbers.
    skill_value_pixels/title_pixels come from count_final_pixels."""
    if len(frames) < 2:
        return StepResult(name, action, len(frames), 0.0, None, {}, False, False, 0.0)

    final = frames[-1]
    diffs = [_diff(f.gray, final.gray) for f in frames[:-1]] + [0.0]
    noise = statistics.mean(diffs[max(0, len(diffs) - 4):-1] or [0.0])

    region_settle = {
        region: _settle_ms(frames, [_diff(f.crops[region], final.crops[region]) for f in frames[:-1]] + [0.0])
        for region in final.crops
    }
    gaps = [(b.t - a.t) * 1000 for a, b in zip(frames, frames[1:])]
    return StepResult(
        name=name, action=action, frames=len(frames), interval_ms=statistics.median(gaps),
        settle_ms=_settle_ms(frames, diffs), region_settle_ms=region_settle,
        had_skills=skill_value_pixels >= _MIN_SKILL_VALUE_PIXELS,
        had_title=title_pixels >= _TITLE_MIN_PIXELS, noise=noise,
    )


# --- the live run ------------------------------------------------------------


def _capture_burst(
    capture_fn: Callable[[], Image.Image], boxes: dict[str, Box],
    now: Callable[[], float], t0: float, duration: float, should_stop: Callable[[], bool],
) -> tuple[list[Frame], Image.Image | None]:
    frames: list[Frame] = []
    last_img = None
    while now() - t0 < duration:
        if should_stop():
            raise StopRequested()
        img = capture_fn()
        t = now() - t0  # stamped the moment the screenshot returns, before any measuring
        frames.append(measure_frame(img, t, boxes))
        last_img = img
    return frames, last_img


def run_probe(
    *,
    capture_fn: Callable[[], Image.Image],
    press_fn: Callable[[str], None],
    region_config: ocr.RegionConfig,
    should_stop: Callable[[], bool] = lambda: False,
    out_dir: Path = LOG_DIR,
    now: Callable[[], float] = time.perf_counter,
    save_images: bool = True,
    cycles: int = 1,
    progress: Callable[[str], None] = lambda msg: None,
) -> ProbeReport:
    """Runs `cycles` full cycles of the key sequence from Material Select,
    capturing after every key (see the module docstring). Writes a CSV of
    every frame, a plain-text summary and each step's final screenshot into
    out_dir, and returns the report.

    press_fn(action) must send the key and return as soon as it is released
    -- no post-press sleep -- so capturing starts right away.
    """
    out_dir.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    boxes = region_boxes(region_config)
    value_box = value_column_box(region_config)
    report = ProbeReport()
    report.csv_path = out_dir / f"probe_{stamp}.csv"
    report.summary_path = out_dir / f"probe_{stamp}_summary.txt"

    with report.csv_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["cycle", "step", "key", "t_ms", "diff_to_final", *(f"diff_{b}" for b in boxes)])
        for cycle in range(1, cycles + 1):
            for index, (name, action, duration) in enumerate(PROBE_STEPS, start=1):
                progress(f"cycle {cycle}/{cycles}, step {index}/{len(PROBE_STEPS)}: {name}")
                t0 = now()
                press_fn(action)
                frames, last_img = _capture_burst(capture_fn, boxes, now, t0, duration, should_stop)
                skill_px, title_px = count_final_pixels(last_img, boxes, value_box) if last_img is not None else (0, 0)
                report.steps.append(analyze_step(name, action, frames, skill_px, title_px))
                for fr in frames:
                    writer.writerow([
                        cycle, name, action, f"{fr.t * 1000:.0f}", f"{_diff(fr.gray, frames[-1].gray):.2f}",
                        *(f"{_diff(fr.crops[b], frames[-1].crops[b]):.2f}" for b in boxes),
                    ])
                if save_images and last_img is not None:
                    path = out_dir / f"probe_{stamp}_c{cycle}_{index:02d}_{name}.png"
                    last_img.save(path)
                    report.images.append(path)

    report.summary_path.write_text("\n".join(summary_lines(report)) + "\n", encoding="utf-8")
    return report


# --- reporting ---------------------------------------------------------------


def _ms(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0f} ms"


def summary_lines(report: ProbeReport) -> list[str]:
    lines = ["Timing probe -- milliseconds after the key press began", ""]
    for step in report.steps:
        lines.append(f"{step.name} (key: {step.action}), {step.frames} frames every ~{step.interval_ms:.0f} ms")
        lines.append(f"    whole screen stopped changing: {_ms(step.settle_ms)}")
        for region, settle in step.region_settle_ms.items():
            lines.append(f"    {region} area stopped changing: {_ms(settle)}")
        lines.append("")

    results = [s for s in report.steps if s.name == RESULTS_STEP]
    if results:
        lines.append("Skill rows on the results screen (the number the pre-read wait must clear):")
        values = []
        for i, step in enumerate(results, start=1):
            settle = step.region_settle_ms.get("skill_rows")
            if step.had_skills:
                values.append(settle)
                lines.append(f"    roll {i}: skill rows finished drawing at {_ms(settle)}")
            else:
                lines.append(f"    roll {i}: no skills on this roll (skill area settled at {_ms(settle)}; not counted)")
        seen = [v for v in values if v is not None]
        if seen:
            lines.append(
                f"    latest across {len(seen)} roll(s) with skills: {max(seen):.0f} ms -- "
                f"the bot currently reads at ~{CURRENT_READ_DELAY_MS} ms"
            )
        else:
            lines.append("    no roll had skills -- run the probe again to catch one")
        lines.append("    (one probe is a sample, not a guarantee: keep a healthy margin above the latest value)")
    return lines
