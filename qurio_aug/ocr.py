"""Crop the Augmented Status skill rows out of a screenshot and OCR them.

Key lessons from testing against the reference screenshots drove this
design:

1. OCR the name and value cells of each row *individually*, heavily
   upscaled. Cropping the whole 3-row skill block at once and OCR'ing it
   as one block loses characters across row boundaries.
2. The single most important trick: **auto-detect the text's actual pixel
   bounding box within a ROI before upscaling and OCR'ing it**, rather
   than trusting the configured fractional box to land exactly on the
   glyphs. A fixed box that's off by even a few pixels vertically was
   enough to make this game's stylized digits (particularly "1", which
   has a distinct top serif) unreadable no matter how much upscaling or
   preprocessing was applied -- but once cropped tight to the actual
   bright-pixel bbox, plain Tesseract reads it correctly first try. This
   also makes the whole pipeline more tolerant of regions.yaml being
   slightly mis-calibrated, which was a concern raised early on.
3. The value cell's digit is OCR'd *separately* from the "Lv +/-" prefix:
   after finding the value text's bbox, columns are scanned for gaps to
   split it into character clusters, and the last cluster (the digit) is
   OCR'd on its own with a digit-only whitelist, trying several psm modes
   until one gives a clean single digit (no one mode was reliable across
   every crop tested -- see _DIGIT_PSM_MODES). The sign is read from the
   whole-text pass, which is reliable on its own.
4. A roll's page layout differs depending on pagination (see regions.yaml
   and RegionConfig): a single-page roll and page 1 of a multi-page roll
   both show the Defense/Slots/Resistance rows, just shifted down when a
   page indicator is present; page 2+ drops those rows entirely and
   starts the skill list right below the indicator.
5. The value cell can be transiently obscured by a "newly changed"
   sparkle decoration right after a roll lands, which can merge the sign
   and digit into one wide, contaminated run. Often less is actually lost
   than it looks -- see lesson 7. `state_machine.py` still waits for the
   UI to settle and retries a few times on a still-unparseable row, since
   a decoration this transient can also just clear on its own.
6. Tesseract is a general-purpose OCR engine tuned for natural document
   fonts; this game uses a fixed bitmap font at a fixed scale, a
   fundamentally different problem, which is why digit reads needed the
   multi-psm dance above at all. Since augment deltas are confirmed to
   only ever be +-1 or +-2 (never higher, per the user's play experience)
   and "1" is the one digit that's caused every real misread bug so far,
   `_template_match_digit` tries a fast, deterministic pixel comparison
   against known-good digit crops in data/digit_templates/ *before*
   falling back to tesseract -- ~600x faster for the digits it covers
   (0.2ms vs ~124ms measured) and not subject to psm-mode flakiness at
   all. Only digits actually confirmed to appear get templates; anything
   without one (currently just "2") falls through to the tesseract path
   unchanged.
7. The panel's border glow turned out to be a continuously *animated*
   effect (it pulses), not a static decoration -- it can bleed a run into
   the value crop's right edge at any time regardless of what else is
   happening, so `select_digit_run` filters it out *by position*
   (BORDER_EXCLUSION_MARGIN from the crop's right edge) rather than by a
   fixed run index, which broke when a sparkle-merged run shifted where
   the border ended up in the run list. Separately, a sparkle bridging the
   sign and digit into one over-wide run doesn't always mean the digit
   itself is unreadable -- sparkle particles are dimmer than solid glyph
   strokes, so `_attempt_debridge` re-scans just the contaminated run at
   stricter brightness thresholds to find the internal gap the normal
   threshold missed. This recovered digits believed fully lost earlier in
   this project's debugging (see `_attempt_debridge`'s docstring).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pytesseract
import yaml
from PIL import Image

from qurio_aug.decision import SkillResult
from qurio_aug.paths import resource_dir
from qurio_aug.skills_db import match_skill_name

CONFIG_DIR = resource_dir("configs")
DIGIT_TEMPLATE_DIR = resource_dir("data", "digit_templates")

_PAGE_RE = re.compile(r"(\d+)\s*/\s*(\d+)")

UPSCALE = 5
DIGIT_UPSCALE = 10
BRIGHT_THRESHOLD = 100  # pixel channel value above which a pixel counts as "text"
BBOX_PADDING = 4
DIGIT_CROP_PADDING = 5  # tesseract needs a little breathing room around a tight glyph crop
# Real digit runs measured ~9-10px wide (at reference resolution) vs. "Lv" at
# ~38px; a run wider than this is a sign something got merged into it (e.g.
# the "newly changed" sparkle decoration overlapping the digit -- see the
# module docstring) rather than a genuine multi-digit delta, so it's treated
# as unparseable rather than trusted.
MAX_DIGIT_RUN_WIDTH = 20
# The panel's border glow bleeds a run in consistently close to the value
# crop's right edge (observed 13px from the edge across two separate live
# failures) -- any run ending within this margin of the crop's right edge
# is excluded as border noise before picking the digit run. See read_row.
BORDER_EXCLUSION_MARGIN = 20
# A "special" (red-bordered) result's glow doesn't always stay within
# BORDER_EXCLUSION_MARGIN -- observed live, a fragment of it landed 26-27px
# from the edge, clear of that margin, and got mistaken for the digit.
# Rather than widen the margin (the glow is animated/variable-extent, so
# any fixed cutoff is guessable at best), select_digit_run instead clusters
# runs by gap size: the real "Lv +N" text's own internal gaps (Lv->sign,
# sign->digit) measured 2-30px across every reference/live sample checked,
# while a stray glow fragment sits 60-74px from whatever precedes it --
# clearly not part of the same text. MAX_INTRA_TEXT_GAP sits between those
# two ranges.
MAX_INTRA_TEXT_GAP = 40
# When the sign and digit merge into one over-wide run, it's usually a
# faint sparkle particle bridging an otherwise-legible gap between them
# (confirmed live: the "1" itself was clearly readable, just a dim
# particle connected it to the "+"). Sparkle particles are dimmer than
# solid glyph strokes, so re-scanning just the contaminated run at
# progressively stricter thresholds can break that bridge while leaving
# real strokes intact. See _attempt_debridge.
_DEBRIDGE_THRESHOLDS = (150, 180, 200, 220)
_DEBRIDGE_CUT_BUFFER = 3

_NAME_CONFIG = "--psm 7"
_VALUE_TEXT_CONFIG = "--psm 7"
# No single psm mode is reliably correct across all digit crops -- testing
# against live captures found cases where --psm 8 read a clean "1" that 6/7/10
# missed, and separately a clean "2" that only 6/7/10 (not 8) could read. So
# a single digit is read by trying each in turn and taking the first clean
# single-digit result, rather than trusting one mode.
_DIGIT_PSM_MODES = (8, 7, 6, 10)
_DIGIT_WHITELIST = "-c tessedit_char_whitelist=0123456789"

# Fast path: pixel template match against known digits, tried before falling
# back to the slower/less consistent tesseract multi-psm approach below.
# Only covers digits actually confirmed to appear (per the user: augment
# deltas are always +-1 or +-2, never higher) and, in practice so far, only
# "1" -- the one digit that's caused every real digit-misread bug this
# project has hit (its flag-serif shape reads inconsistently across psm
# modes; see _DIGIT_PSM_MODES above). "2" has read correctly via tesseract
# every time once other bugs were fixed, so there's no template for it yet
# -- it and anything else fall through to the tesseract path unchanged.
DIGIT_TEMPLATE_MATCH_THRESHOLD = 0.85  # fraction of matching binary pixels required


@lru_cache(maxsize=1)
def _load_digit_templates() -> dict[str, Image.Image]:
    if not DIGIT_TEMPLATE_DIR.exists():
        return {}
    # Binarized at load time, matching what the candidate gets in
    # _template_match_digit -- comparing a binarized candidate against a
    # plain grayscale template almost never matches even for identical
    # source images (confirmed: scored 0.26 against its own source crop
    # before this fix), since few pixels land on exactly 0 or 255.
    return {path.stem: _binarize(Image.open(path)) for path in DIGIT_TEMPLATE_DIR.glob("*.png")}


def _binarize(img: Image.Image, threshold: int = BRIGHT_THRESHOLD) -> Image.Image:
    return img.convert("L").point(lambda p: 255 if p > threshold else 0)


def _template_match_digit(digit_crop: Image.Image) -> str | None:
    """Compare digit_crop against each known digit template (resized to
    the template's size, both binarized so color/font-weight don't
    matter) and return the best match if it clears
    DIGIT_TEMPLATE_MATCH_THRESHOLD, else None to signal "no confident
    template match, use the tesseract fallback".
    """
    templates = _load_digit_templates()
    if not templates:
        return None

    candidate = _binarize(digit_crop)
    best_digit = None
    best_score = 0.0
    for digit, template in templates.items():
        w, h = template.size
        resized = candidate.resize((w, h), Image.LANCZOS)
        resized = resized.point(lambda p: 255 if p > 127 else 0)  # re-binarize post-resize
        t_px, c_px = template.load(), resized.load()
        matches = sum(1 for x in range(w) for y in range(h) if t_px[x, y] == c_px[x, y])
        score = matches / (w * h)
        if score > best_score:
            best_score, best_digit = score, digit

    return best_digit if best_score >= DIGIT_TEMPLATE_MATCH_THRESHOLD else None


# ---------------------------------------------------------------------------
# Region config

@dataclass(frozen=True)
class RowRegions:
    name_box: tuple[float, float, float, float]
    value_box: tuple[float, float, float, float]


@dataclass(frozen=True)
class RegionConfig:
    page_indicator_box: tuple[float, float, float, float]
    row_templates: dict[str, list[RowRegions]]  # "single_page" | "first_of_multi" | "continuation"
    next_page_key: str
    prev_page_key: str
    window_title_hint: str


def _load_rows(raw_rows: list[dict]) -> list[RowRegions]:
    return [RowRegions(tuple(r["name_box"]), tuple(r["value_box"])) for r in raw_rows]


def load_region_config(path: Path | None = None) -> RegionConfig:
    path = path or (CONFIG_DIR / "regions.yaml")
    raw = yaml.safe_load(path.read_text())
    templates = {
        key: _load_rows(raw["row_templates"][key])
        for key in ("single_page", "first_of_multi", "continuation")
    }
    return RegionConfig(
        page_indicator_box=tuple(raw["page_indicator_box"]),
        row_templates=templates,
        next_page_key=raw["next_page_key"],
        prev_page_key=raw["prev_page_key"],
        window_title_hint=raw["window_title_hint"],
    )


# ---------------------------------------------------------------------------
# Pixel-level helpers: crop-to-content is what makes the OCR reliable

def _crop_fraction(img: Image.Image, box: tuple[float, float, float, float]) -> Image.Image:
    w, h = img.size
    x0, y0, x1, y1 = box
    return img.crop((int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h)))


def _upscale(img: Image.Image, factor: int = UPSCALE) -> Image.Image:
    if img.width == 0 or img.height == 0:
        return img
    return img.resize((img.width * factor, img.height * factor), Image.LANCZOS)


def _bright_bbox(img: Image.Image, threshold: int = BRIGHT_THRESHOLD) -> tuple[int, int, int, int] | None:
    """Bounding box of "text" pixels (channel value above threshold) in img,
    or None if the crop is blank. This is what lets us ignore an
    imprecisely-calibrated ROI and OCR just the actual glyphs.
    """
    rgb = img.convert("RGB")
    w, h = rgb.size
    px = rgb.load()
    x0, y0, x1, y1 = w, h, -1, -1
    for y in range(h):
        for x in range(w):
            if max(px[x, y]) > threshold:
                if x < x0:
                    x0 = x
                if x > x1:
                    x1 = x
                if y < y0:
                    y0 = y
                if y > y1:
                    y1 = y
    if x1 < x0:
        return None
    return x0, y0, x1 + 1, y1 + 1


def _pad_bbox(bbox: tuple[int, int, int, int], img: Image.Image, pad: int = BBOX_PADDING) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = bbox
    return (max(0, x0 - pad), max(0, y0 - pad), min(img.width, x1 + pad), min(img.height, y1 + pad))


def _column_runs(img: Image.Image, bbox: tuple[int, int, int, int], threshold: int = BRIGHT_THRESHOLD) -> list[tuple[int, int]]:
    """x-ranges of contiguous columns with bright pixels within bbox --
    splits a text bbox into character/word clusters (e.g. "Lv", "+", "1").
    """
    rgb = img.convert("RGB")
    px = rgb.load()
    x0, y0, x1, y1 = bbox
    runs: list[tuple[int, int]] = []
    cur_start = None
    for x in range(x0, x1):
        has = any(max(px[x, y]) > threshold for y in range(y0, y1))
        if has and cur_start is None:
            cur_start = x
        if not has and cur_start is not None:
            runs.append((cur_start, x))
            cur_start = None
    if cur_start is not None:
        runs.append((cur_start, x1))
    return runs


def _ocr_content(img: Image.Image, config: str, upscale: int = UPSCALE) -> tuple[str, tuple[int, int, int, int] | None]:
    """Auto-crop img to its bright-pixel content (padded), upscale, and OCR
    it. Returns (text, bbox_in_img) -- bbox is None if img was blank.
    """
    bbox = _bright_bbox(img)
    if bbox is None:
        return "", None
    padded = _pad_bbox(bbox, img)
    content = img.crop(padded)
    text = pytesseract.image_to_string(_upscale(content, upscale), config=config).strip()
    return text, bbox


def _ocr_single_digit(img: Image.Image) -> str:
    """Read a tight crop expected to contain exactly one digit.

    Tries a fast pixel template match first (see _template_match_digit) --
    covers the specific digit(s) actually confirmed to appear, currently
    just "1"/"2". Falls back to trying several tesseract psm modes until
    one gives a clean single-digit result (see _DIGIT_PSM_MODES). Returns
    "" if nothing matches -- caller should treat that as unparseable, not
    a guess.

    Tried requiring >=2 psm modes to agree before trusting a digit --
    twice, once universally and once scoped to just digit crops recovered
    via _attempt_debridge (reasoning: a crop that needed recovering from
    contamination is inherently noisier, so demand more confidence).
    *Both* attempts made things worse: in practice, when a crop reads
    correctly -- clean or recovered -- it's usually because exactly *one*
    psm mode produces a clean digit while the others return nothing or
    garbage, not because multiple modes agree. A live recovered-crop case
    that was independently confirmed correct (visible partial glyph
    stroke, template match declining to overreach) only had 1 of 4 modes
    succeed; requiring agreement would have discarded a correct read. A
    misread single digit is a real residual risk either way; see
    SUSPICIOUS_DELTA_THRESHOLD in decision.py for how it's surfaced.
    """
    template_match = _template_match_digit(img)
    if template_match is not None:
        return template_match

    upscaled = _upscale(img, DIGIT_UPSCALE)
    for psm in _DIGIT_PSM_MODES:
        candidate = pytesseract.image_to_string(
            upscaled, config=f"--psm {psm} {_DIGIT_WHITELIST}"
        ).strip()
        if len(candidate) == 1 and candidate.isdigit():
            return candidate
    return ""


# ---------------------------------------------------------------------------
# Row reading

@dataclass(frozen=True)
class RawRow:
    name_text: str
    value_text: str
    digit_text: str  # OCR'd separately from a tight crop around just the digit
    value_blank: bool


def select_digit_run(
    runs: list[tuple[int, int]], crop_width: int
) -> tuple[int, int] | None:
    """Pick which column run is the digit, out of everything found in the
    value cell's bbox ("Lv", the sign, the digit, and sometimes noise).

    The panel's decorative border is an *animated* glow (confirmed: it
    pulses, it's not a one-time fade like the sparkle) that can bleed a
    faint run into the crop's far right edge at any time -- always close
    to the crop's right edge regardless of what else is going on, so it's
    filtered out by position first. What's left is "Lv", the sign, and
    the digit, normally 2-3 runs -- but a heavy sparkle can bridge the gap
    between the sign and the digit, merging them into one run and
    shifting *where* the digit content ends up.

    That's not the only shape the border glow shows up in, though: on a
    "special" (red-bordered) result, a fragment of it can land far enough
    inside the crop to dodge BORDER_EXCLUSION_MARGIN entirely, adding a
    *4th* run after "Lv", sign, and digit. Blindly taking the last
    remaining run (which correctly handles both the clean 3-run and
    merged 2-run cases) grabs that fragment instead of the real digit in
    this case. Taking a fixed index instead was tried before and also
    broke, for the same underlying reason: the merged-run case shifts
    where "the digit" sits in the list, so no fixed position is right for
    every case.

    What's actually reliable is the *gap* between runs: "Lv", the sign,
    and the digit form one continuous piece of rendered text, so the gaps
    between them are small and consistent (see MAX_INTRA_TEXT_GAP). A
    stray glow fragment isn't part of that text -- it's independently
    positioned -- so it sits behind a much bigger gap. Clustering runs
    from the left and cutting the cluster off at the first oversized gap
    isolates the real text regardless of how many stray fragments follow
    it or how many runs the text itself happens to occupy.

    Returns None if there's nothing plausible left (e.g. only "Lv" and
    border noise, no sign/digit content at all).
    """
    content_runs = [r for r in runs if (crop_width - r[1]) > BORDER_EXCLUSION_MARGIN]
    if len(content_runs) < 2:  # need at least "Lv" + something after it
        return None
    cluster = [content_runs[0]]
    for run in content_runs[1:]:
        if run[0] - cluster[-1][1] > MAX_INTRA_TEXT_GAP:
            break
        cluster.append(run)
    if len(cluster) < 2:
        return None
    return cluster[-1]


def _attempt_debridge(
    value_crop: Image.Image, digit_run: tuple[int, int], value_bbox: tuple[int, int, int, int]
) -> tuple[int, int] | None:
    """When digit_run is wider than MAX_DIGIT_RUN_WIDTH, try to recover a
    narrower digit-only run from within it rather than giving up outright
    -- see _DEBRIDGE_THRESHOLDS for why this works. Returns a narrower
    (x0, x1) to retry OCR on, or None if no plausible split was found.

    This recovers more than expected: even step-4-augmentation-result.png's
    Artillery row -- cited throughout this project's early debugging as
    the canonical "too sparkle-occluded to recover" example -- now
    recovers "+1" here, and it holds up under scrutiny (a partial green
    "1" stroke is visible beneath the sparkle on inspection, and 3 of 4
    tesseract psm modes independently agree). There's no hard guarantee
    debridging can't ever manufacture a wrong digit from a contaminated
    run, but a genuinely fully-obscured run (nothing to split at any
    threshold) still correctly falls through to unparseable.
    """
    for threshold in _DEBRIDGE_THRESHOLDS:
        sub_bbox = (digit_run[0], value_bbox[1], digit_run[1], value_bbox[3])
        sub_runs = _column_runs(value_crop, sub_bbox, threshold=threshold)
        if len(sub_runs) < 2:
            continue
        cut_x = max(digit_run[0], sub_runs[-1][0] - _DEBRIDGE_CUT_BUFFER)
        candidate = (cut_x, digit_run[1])
        if candidate[1] - candidate[0] <= MAX_DIGIT_RUN_WIDTH:
            return candidate
    return None


def read_row(screenshot: Image.Image, row: RowRegions) -> RawRow:
    name_crop = _crop_fraction(screenshot, row.name_box)
    value_crop = _crop_fraction(screenshot, row.value_box)

    name_text, _ = _ocr_content(name_crop, _NAME_CONFIG)
    value_text, value_bbox = _ocr_content(value_crop, _VALUE_TEXT_CONFIG)

    digit_text = ""
    if value_bbox is not None and "none" not in value_text.lower():
        runs = _column_runs(value_crop, value_bbox)
        digit_run = select_digit_run(runs, value_crop.width)
        if digit_run is not None:
            run_width = digit_run[1] - digit_run[0]
            if run_width > MAX_DIGIT_RUN_WIDTH:
                recovered = _attempt_debridge(value_crop, digit_run, value_bbox)
                if recovered is not None:
                    digit_run = recovered
                    run_width = digit_run[1] - digit_run[0]
            if run_width <= MAX_DIGIT_RUN_WIDTH:
                # Pad generously on all sides -- tesseract reads a tight
                # single-glyph crop much less reliably than one with a
                # little margin around it (confirmed by testing).
                x0 = max(0, digit_run[0] - DIGIT_CROP_PADDING)
                x1 = min(value_crop.width, digit_run[1] + DIGIT_CROP_PADDING)
                y0 = max(0, value_bbox[1] - DIGIT_CROP_PADDING)
                y1 = min(value_crop.height, value_bbox[3] + DIGIT_CROP_PADDING)
                digit_text = _ocr_single_digit(value_crop.crop((x0, y0, x1, y1)))

    return RawRow(
        name_text=name_text,
        value_text=value_text,
        digit_text=digit_text,
        value_blank=value_bbox is None,
    )


def parse_value(raw: RawRow) -> tuple[int | None, bool]:
    """Returns (delta, removed). delta is None if unparseable."""
    if raw.value_blank:
        return None, False
    if "none" in raw.value_text.lower():
        return None, True
    sign = "-" if "-" in raw.value_text else "+"
    if not raw.digit_text.isdigit():
        return None, False
    value = int(raw.digit_text)
    return (value if sign == "+" else -value), False


EMPTY_NAME_LEN_THRESHOLD = 3  # shorter than this + blank value => treat as a blank slot


@dataclass(frozen=True)
class ParsedRow:
    skill: SkillResult | None  # a real result, when both name and value parsed
    blank: bool  # True: this row slot legitimately has nothing in it
    unparseable: bool  # True: there's clearly *something* here but we couldn't read it


def parse_row(raw: RawRow) -> ParsedRow:
    has_name_text = len(raw.name_text.strip()) > EMPTY_NAME_LEN_THRESHOLD

    # A real skill entry always has a legible name -- if there isn't one,
    # this slot is blank, full stop, regardless of what the value side's
    # bbox detection found. Requiring the value to *also* show nothing
    # used to break on a live capture: a genuinely empty slot's value box
    # picked up a faint stray artifact (the panel's decorative glow
    # bleeding in), which made value_blank False and skipped this check
    # entirely, sending an empty name into skill matching where it could
    # only ever fail as "unparseable" rather than correctly read as blank.
    if not has_name_text:
        return ParsedRow(skill=None, blank=True, unparseable=False)

    skill, score = match_skill_name(raw.name_text)
    delta, removed = parse_value(raw)

    if skill is None or (not removed and delta is None):
        return ParsedRow(skill=None, blank=False, unparseable=True)

    result = SkillResult(name=skill.name, delta=delta or 0, removed=removed)
    return ParsedRow(skill=result, blank=False, unparseable=False)


def read_page(screenshot: Image.Image, rows: list[RowRegions]) -> list[ParsedRow]:
    """One ParsedRow per row slot on the current page -- see ParsedRow for
    how to distinguish a legitimately blank slot from a failed read. Pass
    the row template matching the current page layout (see
    RegionConfig.row_templates / state_machine.py's page-indicator logic).
    """
    return [parse_row(read_row(screenshot, row)) for row in rows]


# ---------------------------------------------------------------------------
# Pagination indicator ("<Q 1/2 E>")

def read_page_indicator(screenshot: Image.Image, config: RegionConfig) -> tuple[int, int] | None:
    """Returns (current_page, total_pages), or None if no indicator is
    present (i.e. this roll fits on a single page).

    The indicator renders as "<Q N/M E>" -- boxed Q/E key glyphs flanking
    the page numbers, e.g. 7 column runs: arrow, [Q], N, /, M, [E], arrow.
    OCR'ing it as one string is unreliable (the bracketed "E" gets read as
    part of the number, e.g. "1/2" -> "1/28"; other times the whole string
    just fails to read at all despite N and M being individually clear).
    So each digit is isolated by its own run (same tight-crop-plus-padding
    approach as the skill value digits in read_row) and OCR'd separately.

    Only single-digit page numbers are supported (i.e. exactly 7 runs) --
    comfortably enough for the ~7-augment max observed in the datamine
    notes (at most 3 pages of 3). Anything else is treated as "no
    indicator" rather than guessed.
    """
    crop = _crop_fraction(screenshot, config.page_indicator_box)
    bbox = _bright_bbox(crop)
    if bbox is None:
        return None
    runs = _column_runs(crop, bbox)
    if len(runs) != 7:
        return None  # not the expected arrow/[Q]/N//M/[E]/arrow structure

    def read_digit_run(run: tuple[int, int]) -> int | None:
        x0 = max(0, run[0] - DIGIT_CROP_PADDING)
        x1 = min(crop.width, run[1] + DIGIT_CROP_PADDING)
        y0 = max(0, bbox[1] - DIGIT_CROP_PADDING)
        y1 = min(crop.height, bbox[3] + DIGIT_CROP_PADDING)
        text = _ocr_single_digit(crop.crop((x0, y0, x1, y1)))
        return int(text) if text else None

    current = read_digit_run(runs[2])
    total = read_digit_run(runs[4])
    if current is None or total is None:
        return None
    return current, total
