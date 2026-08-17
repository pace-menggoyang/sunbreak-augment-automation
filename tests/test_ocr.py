"""Offline tests for ocr.parse_row's blank/unparseable classification,
select_digit_run's border-exclusion logic, and the debridge recovery path
for sparkle-contaminated digits, using synthetic RawRow fixtures, real
column-run coordinates, and real value-crop images -- all measured from
live captures."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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
