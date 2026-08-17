"""Offline tests for the digit template-matching fast path in ocr.py.

Fixtures in tests/fixtures/ are real digit crops pulled from the
reference screenshots via the exact same pipeline production code uses
(_bright_bbox -> _column_runs -> border-exclusion -> padding), not
synthetic images:
  - digit_1_sample.png is the same crop data/digit_templates/1.png was
    built from (a skill value's "Muck Resistance +1").
  - digit_1_sample_independent.png is a *different* row's "+1" (Critical
    Eye) to confirm the template generalizes rather than just matching
    its own source image.
  - digit_2_sample.png is the same crop data/digit_templates/2.png was
    built from -- but note this came from the page indicator's "2/2"
    (a different UI element than a skill value), not yet a confirmed
    skill-value "+2"/"-2" capture. If a live "2" digit ever misreads,
    that mismatch is the first thing to check.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image

from qurio_aug import ocr

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_templates_load():
    templates = ocr._load_digit_templates()
    assert "1" in templates
    assert "2" in templates


def test_1_matches_its_own_source_crop():
    img = Image.open(FIXTURES / "digit_1_sample.png")
    assert ocr._template_match_digit(img) == "1"


def test_1_generalizes_to_independent_sample():
    img = Image.open(FIXTURES / "digit_1_sample_independent.png")
    assert ocr._template_match_digit(img) == "1"


def test_2_matches_its_own_source_crop():
    img = Image.open(FIXTURES / "digit_2_sample.png")
    assert ocr._template_match_digit(img) == "2"


def test_1_and_2_do_not_cross_match():
    one = Image.open(FIXTURES / "digit_1_sample.png")
    two = Image.open(FIXTURES / "digit_2_sample.png")
    assert ocr._template_match_digit(one) != "2"
    assert ocr._template_match_digit(two) != "1"


def test_ocr_single_digit_uses_template_for_1():
    img = Image.open(FIXTURES / "digit_1_sample.png")
    assert ocr._ocr_single_digit(img) == "1"


def test_ocr_single_digit_uses_template_for_2():
    img = Image.open(FIXTURES / "digit_2_sample.png")
    assert ocr._ocr_single_digit(img) == "2"


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
