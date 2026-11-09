"""Offline tests for RunStats -- pure counting and formatting."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qurio_aug.ocr import ParsedRow
from qurio_aug.run_stats import RunStats


def _row(digit_source="", name_source=""):
    return ParsedRow(skill=None, blank=False, unparseable=False, digit_source=digit_source, name_source=name_source)


def test_record_page_groups_tesseract_psm_modes_into_one_family():
    stats = RunStats()
    stats.record_page([_row("template"), _row("tesseract:psm8"), _row("tesseract:psm7"), _row("")])
    assert dict(stats.digit_sources) == {"template": 1, "tesseract": 2}


def test_record_page_counts_implausible_digits_and_name_recovery():
    stats = RunStats()
    stats.record_page([_row("tesseract:psm7!", "icon-stripped"), _row("template")])
    assert stats.implausible_digits == 1
    assert dict(stats.digit_sources) == {"tesseract": 1, "template": 1}
    assert dict(stats.name_sources) == {"icon-stripped": 1}


def test_summary_for_a_clean_run_is_a_single_headline():
    assert RunStats().summary_lines(attempts=0, elapsed=0.0) == ["Run summary: 0 attempt(s) in 0m00s (0.0/min)"]


def test_summary_rate_and_elapsed():
    line = RunStats().summary_lines(attempts=30, elapsed=60.0)[0]
    assert "30 attempt(s)" in line and "1m00s" in line and "30.0/min" in line


def test_summary_lists_every_section_that_has_data():
    stats = RunStats()
    stats.record_page([_row("tesseract:psm7!", "icon-stripped"), _row("template")])
    stats.page_retries = 5
    stats.indicator_retries = 3
    stats.indicator_unresolved = 1
    stats.dialogs["apply_results"] += 2
    stats.record_slowdown(0.18, 0.225)
    stats.record_slowdown(0.225, 0.28125)
    text = "\n".join(stats.summary_lines(attempts=40, elapsed=90.0))
    assert "digit reads: " in text and "1 implausible" in text
    assert "name reads needing recovery: icon-stripped 1" in text
    assert "page reads 5" in text and "page indicator 3 (1 unresolved)" in text
    assert "apply_results x2" in text
    assert "key delay raised 0.180s -> 0.281s" in text  # first old -> last new


def test_summary_flags_an_unusually_high_share_of_empty_rolls():
    stats = RunStats()
    stats.blank_attempts, stats.max_blank_streak = 48, 48
    text = "\n".join(stats.summary_lines(attempts=50, elapsed=100.0))
    assert "empty rolls (no skills at all): 48 of 50 (96%), longest streak 48" in text
    assert "unusually high" in text


def test_summary_does_not_flag_a_normal_share_of_empty_rolls():
    stats = RunStats()
    stats.blank_attempts, stats.max_blank_streak = 8, 2  # ~17% of 50 -- ordinary
    text = "\n".join(stats.summary_lines(attempts=50, elapsed=100.0))
    assert "empty rolls (no skills at all): 8 of 50 (16%)" in text
    assert "unusually high" not in text


def test_summary_does_not_cry_wolf_on_a_tiny_sample():
    stats = RunStats()
    stats.blank_attempts, stats.max_blank_streak = 3, 3  # 3 of 3, but only 3 attempts
    assert "unusually high" not in "\n".join(stats.summary_lines(attempts=3, elapsed=6.0))


def test_summary_omits_sections_with_nothing_to_report():
    stats = RunStats()
    stats.record_page([_row("template")])
    text = "\n".join(stats.summary_lines(attempts=1, elapsed=2.0))
    assert "retries" not in text and "dialogs" not in text and "name reads" not in text


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
