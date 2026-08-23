"""Offline tests for ocr.parse_row's blank/unparseable classification,
select_digit_run's border-exclusion logic, and the debridge recovery path
for sparkle-contaminated digits, using synthetic RawRow fixtures, real
column-run coordinates, and real value-crop images -- all measured from
live captures."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytesseract
from PIL import Image

from qurio_aug import ocr
from qurio_aug.ocr import RawRow, parse_row, select_digit_run

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_empty_name_and_value_is_blank():
    raw = RawRow(name_text="", value_text="", digit_text="", value_blank=True)
    parsed = parse_row(raw)
    assert parsed.blank and not parsed.unparseable and parsed.skill is None


def test_empty_name_with_stray_value_artifact_is_still_blank():
    # Observed live: a genuinely empty row slot's value box picked up a
    # faint stray artifact (the panel's decorative glow bleeding in),
    # producing value_blank=False even though there's no real skill here.
    # A real skill entry always has a legible name, so an empty name
    # should mean "blank" regardless of what the value side found.
    raw = RawRow(name_text="", value_text="|", digit_text="", value_blank=False)
    parsed = parse_row(raw)
    assert parsed.blank and not parsed.unparseable and parsed.skill is None


def test_legible_name_and_value_parses_normally():
    raw = RawRow(name_text="Artillery", value_text="Lv +1", digit_text="1", value_blank=False)
    parsed = parse_row(raw)
    assert not parsed.blank and not parsed.unparseable
    assert parsed.skill.name == "Artillery" and parsed.skill.delta == 1


def test_legible_name_but_unreadable_value_is_unparseable():
    raw = RawRow(name_text="Artillery", value_text="Lv +", digit_text="", value_blank=False)
    parsed = parse_row(raw)
    assert not parsed.blank and parsed.unparseable and parsed.skill is None


def test_none_removal_parses_correctly():
    raw = RawRow(name_text="Critical Boost", value_text="None", digit_text="", value_blank=False)
    parsed = parse_row(raw)
    assert not parsed.blank and not parsed.unparseable
    assert parsed.skill.name == "Critical Boost" and parsed.skill.removed


# --- select_digit_run: real column-run coordinates measured from live
# captures during this project's debugging, at the ~209px-wide value crop
# used throughout (single_page/first_of_multi templates). ---

CROP_WIDTH = 209


def test_select_digit_run_clean_case():
    # "Lv +1", no artifacts (Muck Resistance +1).
    runs = [(31, 69), (95, 111), (115, 125)]
    assert select_digit_run(runs, CROP_WIDTH) == (115, 125)


def test_select_digit_run_excludes_trailing_border_bleed():
    # "Lv +1" plus the panel's animated border glow bleeding a run into
    # the last ~13px of the crop (Coalescence +1) -- taking the *last*
    # run naively would grab the border, not the digit.
    runs = [(28, 62), (90, 106), (110, 120), (190, 196)]
    assert select_digit_run(runs, CROP_WIDTH) == (110, 120)


def test_select_digit_run_handles_sparkle_merged_plus_border():
    # A heavy sparkle bridged the sign and digit into one run AND the
    # border bled in too (Sneak Attack, the case that motivated this
    # function's extraction) -- border must still be excluded even though
    # run count dropped from 4 to 3, which broke a fixed-index approach
    # (it grabbed the border because the merge shifted indices).
    runs = [(28, 62), (86, 120), (190, 196)]
    assert select_digit_run(runs, CROP_WIDTH) == (86, 120)


def test_select_digit_run_excludes_stray_glow_fragment_beyond_margin():
    # A "special" (red-bordered) result's glow doesn't always stay inside
    # BORDER_EXCLUSION_MARGIN -- observed live (Resentment +1 and Defense
    # Boost +1, same capture), a fragment landed 26-27px from the edge,
    # clear of the margin, adding a spurious 4th run after "Lv", sign, and
    # the real digit. Taking the last remaining run grabbed that fragment
    # instead -- misread as "4" on one row, unparseable on the other.
    runs = [(28, 62), (90, 106), (110, 120), (180, 182), (190, 196)]
    assert select_digit_run(runs, CROP_WIDTH) == (110, 120)


def test_select_digit_run_excludes_wider_stray_glow_fragment():
    # Same failure, second row from the same capture -- the stray fragment
    # here was wide enough (12px) to not be dismissable by width alone,
    # confirming the fix has to be gap-based, not a width filter.
    runs = [(28, 62), (90, 106), (110, 120), (170, 182), (184, 202)]
    assert select_digit_run(runs, CROP_WIDTH) == (110, 120)


def test_select_digit_run_none_when_only_border_noise():
    # "Lv" plus just border bleed, no real sign/digit content at all.
    runs = [(28, 62), (190, 196)]
    assert select_digit_run(runs, CROP_WIDTH) is None


def test_select_digit_run_none_when_empty():
    assert select_digit_run([], CROP_WIDTH) is None


# --- _attempt_debridge + full read_row, against real sparkle-contaminated
# value crops pulled from live failures. Both of these were originally
# unparseable before debridge recovery was added -- value_crop_heavy_sparkle
# is the Artillery row from step-4-augmentation-result.png, cited
# throughout this project's earlier debugging as the canonical
# "too occluded to recover" example. It turns out not to be: a partial
# green "1" stroke is visible beneath the sparkle on inspection, and 3 of
# 4 tesseract psm modes independently agree on "1" once debridged. ---


def test_debridge_recovers_moderately_contaminated_digit():
    img = Image.open(FIXTURES / "value_crop_sparkle_recoverable.png")
    bbox = ocr._bright_bbox(img)
    runs = ocr._column_runs(img, bbox)
    digit_run = select_digit_run(runs, img.width)
    assert digit_run[1] - digit_run[0] > ocr.MAX_DIGIT_RUN_WIDTH  # confirms contamination

    recovered = ocr._attempt_debridge(img, digit_run, bbox)
    assert recovered is not None
    assert recovered[1] - recovered[0] <= ocr.MAX_DIGIT_RUN_WIDTH


def test_debridge_recovers_heavily_contaminated_digit():
    img = Image.open(FIXTURES / "value_crop_heavy_sparkle_recoverable.png")
    bbox = ocr._bright_bbox(img)
    runs = ocr._column_runs(img, bbox)
    digit_run = select_digit_run(runs, img.width)
    assert digit_run[1] - digit_run[0] > ocr.MAX_DIGIT_RUN_WIDTH

    recovered = ocr._attempt_debridge(img, digit_run, bbox)
    assert recovered is not None


def test_debridged_digit_reads_correctly_end_to_end():
    # Same as test_debridge_recovers_moderately_contaminated_digit, one
    # step further: the recovered run actually OCRs as the correct digit.
    img = Image.open(FIXTURES / "value_crop_sparkle_recoverable.png")
    value_bbox = ocr._bright_bbox(img)
    runs = ocr._column_runs(img, value_bbox)
    digit_run = select_digit_run(runs, img.width)
    recovered = ocr._attempt_debridge(img, digit_run, value_bbox)

    x0 = max(0, recovered[0] - ocr.DIGIT_CROP_PADDING)
    x1 = min(img.width, recovered[1] + ocr.DIGIT_CROP_PADDING)
    y0 = max(0, value_bbox[1] - ocr.DIGIT_CROP_PADDING)
    y1 = min(img.height, value_bbox[3] + ocr.DIGIT_CROP_PADDING)
    digit_text = ocr._ocr_single_digit(img.crop((x0, y0, x1, y1)))
    assert digit_text == "1"


# --- Community-reported bug: a "Lv +1" gain never resolving across all 8
# retries, in every case a bright pink/white sparkle overlapping the
# digit rather than a dim one _attempt_debridge's brightness thresholds
# can see past. Fixtures are real value-cell crops from 2 of the 5
# community-submitted failure captures that motivated this fix.

def test_color_debridge_fixes_a_wrong_cut_brightness_debridge_makes():
    # This fixture is a real case where _attempt_debridge (brightness)
    # returns a *non-None* but wrong, too-narrow slice -- (88, 93), which
    # OCRs as "4" -- because it reuses digit_run's own right edge (93),
    # not realizing that edge came from the sparkle, not the glyph. The
    # real "1" is at columns 73-81. _attempt_color_debridge doesn't make
    # this mistake since it trusts the green-only run's own bounds.
    img = Image.open(FIXTURES / "value_crop_bridged_sparkle_color_recoverable.png")
    bbox = ocr._bright_bbox(img)
    runs = ocr._column_runs(img, bbox)
    digit_run = select_digit_run(runs, img.width)
    assert digit_run[1] - digit_run[0] > ocr.MAX_DIGIT_RUN_WIDTH  # confirms contamination

    wrong = ocr._attempt_debridge(img, digit_run, bbox)
    assert wrong is not None and wrong[1] - wrong[0] <= ocr.MAX_DIGIT_RUN_WIDTH  # "succeeds" but wrongly

    recovered = ocr._attempt_color_debridge(img, digit_run, bbox)
    assert recovered is not None
    assert recovered[1] - recovered[0] <= ocr.MAX_DIGIT_RUN_WIDTH
    assert recovered != wrong


def test_read_row_recovers_bridged_sparkle_via_color_debridge():
    row = ocr.RowRegions(name_box=(0, 0, 1, 1), value_box=(0, 0, 1, 1))
    img = Image.open(FIXTURES / "value_crop_bridged_sparkle_color_recoverable.png")
    raw = ocr.read_row(img, row)
    assert raw.digit_text == "1"


def test_recover_sparkle_contaminated_digit_cleans_a_narrow_already_correct_run():
    # Here digit_run is already within MAX_DIGIT_RUN_WIDTH (the sparkle
    # sits *on* the digit, not bridging it to the sign) -- neither
    # debridge function ever triggers. Confirmed live: 4 separate real
    # community failures had exactly this shape, all failing every
    # existing recognition attempt on the untouched crop.
    img = Image.open(FIXTURES / "value_crop_sparkle_on_digit_recoverable.png")
    bbox = ocr._bright_bbox(img)
    runs = ocr._column_runs(img, bbox)
    digit_run = select_digit_run(runs, img.width)
    assert digit_run[1] - digit_run[0] <= ocr.MAX_DIGIT_RUN_WIDTH  # not oversized -- debridge never fires

    x0 = max(0, digit_run[0] - ocr.DIGIT_CROP_PADDING)
    x1 = min(img.width, digit_run[1] + ocr.DIGIT_CROP_PADDING)
    y0 = max(0, bbox[1] - ocr.DIGIT_CROP_PADDING)
    y1 = min(img.height, bbox[3] + ocr.DIGIT_CROP_PADDING)
    digit_crop = img.crop((x0, y0, x1, y1))

    assert ocr._recover_sparkle_contaminated_digit(digit_crop) == "1"


def test_read_row_recovers_sparkle_on_digit_end_to_end():
    row = ocr.RowRegions(name_box=(0, 0, 1, 1), value_box=(0, 0, 1, 1))
    img = Image.open(FIXTURES / "value_crop_sparkle_on_digit_recoverable.png")
    raw = ocr.read_row(img, row)
    assert raw.digit_text == "1"


def test_is_green_digit_pixel_separates_green_stroke_from_sparkle():
    # Measured directly from the fixtures above: real green digit stroke
    # pixels ranged (0, 100-240, 0); real sparkle pixels measured
    # included (100, 60, 80) and a near-white anti-aliased edge (140,
    # 140, 140) -- both should read as "not a green digit pixel".
    assert ocr._is_green_digit_pixel(0, 200, 0)
    assert ocr._is_green_digit_pixel(0, 101, 2)
    assert not ocr._is_green_digit_pixel(100, 60, 80)
    assert not ocr._is_green_digit_pixel(140, 140, 140)


# --- indicator_region_ambiguous: distinguishes a genuine (if
# glow-corrupted) page indicator from a genuinely single-page roll, whose
# indicator box can still pick up a narrow sliver of an adjacent row's
# text (single_page's layout shifts rows up to fill the space a real
# indicator would occupy). Fixtures are real indicator-box crops pulled
# from a live failure and a real single-page reference -- a "full box"
# config makes _crop_fraction a no-op so the fixture is used as-is. ---

_FULL_BOX_CONFIG = ocr.RegionConfig(
    page_indicator_box=(0, 0, 1, 1),
    results_title_box=(0, 0, 1, 1),
    row_templates={"single_page": [], "first_of_multi": [], "continuation": []},
    next_page_key="e",
    prev_page_key="q",
    window_title_hint="dummy",
)


def test_indicator_region_ambiguous_true_for_real_corrupted_indicator():
    # A genuine two-page roll's indicator, corrupted by the panel's
    # animated red border glow enough that read_page_indicator couldn't
    # parse it -- but it's clearly still *there*, spanning most of the
    # box's width, not a stray fragment.
    img = Image.open(FIXTURES / "page_indicator_ambiguous.png")
    assert ocr.indicator_region_ambiguous(img, _FULL_BOX_CONFIG)


def test_indicator_region_ambiguous_false_for_single_page_bleedthrough():
    # A genuinely single-page roll: no real indicator exists, but a
    # sliver of the row below ("Defense") bleeds into the same box since
    # single_page's layout sits higher than first_of_multi's. Narrow
    # enough that it must not be mistaken for a corrupted real indicator.
    img = Image.open(FIXTURES / "page_indicator_single_page_bleedthrough.png")
    assert not ocr.indicator_region_ambiguous(img, _FULL_BOX_CONFIG)


def test_indicator_region_ambiguous_false_when_truly_blank():
    img = Image.new("RGB", (370, 65), (20, 20, 20))
    assert not ocr.indicator_region_ambiguous(img, _FULL_BOX_CONFIG)


# --- is_augmentation_results_screen: confirms the game actually landed on
# STATE4 (Augmentation Results) at all. Fixtures are real crops of the
# title-box region -- one from a reference screenshot known to show the
# results screen, one from a real live failure that turned out to
# genuinely be a different screen (STATE3, "Requires materials... Proceed?")
# that state_machine had been misreading as an unparseable roll. ---


def test_is_augmentation_results_screen_true_for_real_results_screen():
    img = Image.open(FIXTURES / "results_title_present.png")
    assert ocr.is_augmentation_results_screen(img, _FULL_BOX_CONFIG)


def test_is_augmentation_results_screen_false_for_real_wrong_screen():
    img = Image.open(FIXTURES / "results_title_wrong_screen.png")
    assert not ocr.is_augmentation_results_screen(img, _FULL_BOX_CONFIG)


# --- read_page: rows are read concurrently on a thread pool for speed
# (measured 2x faster on a real page read) -- these confirm that doesn't
# scramble results across rows or corrupt the shared source image.
# Fixture is a real 3-row crop with known, distinct content per row
# (Artillery +1 / Diversion +1 / Critical Boost None), small enough to
# commit directly rather than depending on the gitignored step-references.

_THREE_ROWS_CONFIG = [
    ocr.RowRegions((0.0433, 0.0428, 0.6273, 0.2015), (0.6424, 0.1887, 0.9567, 0.3607)),
    ocr.RowRegions((0.0433, 0.3342, 0.6273, 0.4929), (0.6424, 0.4801, 0.9567, 0.6525)),
    ocr.RowRegions((0.0433, 0.6256, 0.6273, 0.7852), (0.6424, 0.7852, 0.9567, 0.9572)),
]


def _read_names(img: Image.Image) -> list[str]:
    page = ocr.read_page(img, _THREE_ROWS_CONFIG)
    return [p.skill.name if p.skill else ("blank" if p.blank else "UNPARSEABLE") for p in page]


def test_read_page_rows_come_back_in_order_not_scrambled_by_threads():
    # Opens a *fresh* (not yet .load()'d) image each trial, deliberately --
    # this is exactly what exposed the real bug this test guards against
    # (see read_page's docstring): multiple threads racing to be the first
    # to decode a lazily-loaded PIL image corrupted the read entirely
    # ("image file is truncated"), not just scrambled row order.
    for _ in range(10):
        img = Image.open(FIXTURES / "three_rows_reference.png")
        assert _read_names(img) == ["Artillery", "Diversion", "Critical Boost"]


def test_read_page_empty_rows_returns_empty_without_touching_the_image():
    assert ocr.read_page(Image.new("RGB", (10, 10)), []) == []


# --- _run_tesseract: resilience to a transient pytesseract/tesseract
# subprocess failure, observed live after ~400 rapid sequential OCR calls
# in one run -- tesseract's temp output file was gone by the time
# pytesseract tried to read it back (FileNotFoundError), which crashed the
# entire process instead of just failing that one OCR call. ---


def test_run_tesseract_treats_missing_temp_file_as_no_text_found():
    original = pytesseract.image_to_string
    pytesseract.image_to_string = lambda *a, **kw: (_ for _ in ()).throw(
        FileNotFoundError("simulated missing tesseract temp output file")
    )
    try:
        result = ocr._run_tesseract(Image.new("RGB", (10, 10)), "--psm 7")
    finally:
        pytesseract.image_to_string = original
    assert result == ""


def test_run_tesseract_treats_tesseract_error_as_no_text_found():
    original = pytesseract.image_to_string

    def boom(*a, **kw):
        raise pytesseract.pytesseract.TesseractError(1, "simulated tesseract failure")

    pytesseract.image_to_string = boom
    try:
        result = ocr._run_tesseract(Image.new("RGB", (10, 10)), "--psm 7")
    finally:
        pytesseract.image_to_string = original
    assert result == ""


def test_run_tesseract_passes_through_real_text_normally():
    # Confirms the wrapper isn't swallowing legitimate results -- only
    # the two specific failure modes above.
    original = pytesseract.image_to_string
    pytesseract.image_to_string = lambda *a, **kw: "  Artillery  "
    try:
        result = ocr._run_tesseract(Image.new("RGB", (10, 10)), "--psm 7")
    finally:
        pytesseract.image_to_string = original
    assert result == "Artillery"


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
