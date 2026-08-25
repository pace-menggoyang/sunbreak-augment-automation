"""Offline tests for the page-indicator-driven pagination logic in
state_machine.read_full_roll, using monkeypatched ocr.read_page /
ocr.read_page_indicator so no real screenshots/OCR/game are needed.
"""
import io
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image

from qurio_aug import ocr, state_machine
from qurio_aug.decision import Decision, Goal, Profile, RequiredSkill, SkillResult

# A real (if trivial) image -- state_machine.read_full_roll now saves the
# screenshot to logs/ on an UnreadableRollError, so the fake screenshot
# needs to be something .save()-able, not a bare object().
_FAKE_SCREENSHOT = Image.new("RGB", (10, 10))


def _goal(protected_skills=frozenset(), required=None):
    """Minimal Goal for exercising read_full_roll's page-skip logic.

    With `required=None`, the one profile has no required_skills at all,
    so decision.any_profile_reachable is vacuously True regardless of what
    page 1 shows -- i.e. this goal never triggers the "page 1 gains can't
    satisfy any profile" early exit, only the protected_skills-driven
    exits. Pass `required` (a skill name) to test that exit specifically.
    """
    req = (RequiredSkill(required, min_level=1),) if required else ()
    return Goal(
        name="test",
        augment_type="skills_plus",
        profiles=(Profile(required_skills=req),),
        protected_skills=protected_skills,
    )


class FakeGame:
    def __init__(self):
        self.next_calls = 0
        self.prev_calls = 0
        self.park_calls = 0
        self.trigger_calls = 0
        self.accept_calls = 0
        self.reroll_calls = 0

    def next_page(self):
        self.next_calls += 1

    def prev_page(self):
        self.prev_calls += 1

    def park_mouse(self, window_bounds):
        self.park_calls += 1

    # Only exercised by the state_machine.run() tests below -- read_full_roll
    # itself never calls these.
    def trigger_roll(self):
        self.trigger_calls += 1

    def accept_macro(self):
        self.accept_calls += 1

    def reroll_macro(self):
        self.reroll_calls += 1


DUMMY_WINDOW_BOUNDS = (0.0, 0.0, 1440.0, 900.0)


def _row(name, delta=1):
    return ocr.ParsedRow(skill=SkillResult(name, delta=delta), blank=False, unparseable=False)


def _removed_row(name):
    return ocr.ParsedRow(skill=SkillResult(name, removed=True), blank=False, unparseable=False)


def _blank():
    return ocr.ParsedRow(skill=None, blank=True, unparseable=False)


def _unparseable():
    return ocr.ParsedRow(skill=None, blank=False, unparseable=True)


class Script:
    """Each entry is (indicator_result, page_rows) for one logical page,
    indexed by how far navigation has actually gotten -- not by call
    count -- so that read_page_indicator (called by _wait_for_page
    *before* read_page, to confirm a next_page() press actually landed)
    reports the page navigation is really on, the same as read_page does.
    `region_config` in the RegionConfig calls is ignored -- row_templates
    content doesn't matter since read_page is faked directly.

    Pass `game` (the same FakeGame instance driving next_page()/
    prev_page()) so the current page tracks its net next_calls -
    prev_calls; omit it for single-page scripts that never navigate,
    where the index is always 0 regardless.

    `results_screen_present` (default True) stubs
    ocr.is_augmentation_results_screen -- default True means it's never
    the reason an existing test's error message changes, and it's never
    a real tesseract call in an otherwise-offline test. Pass False to
    specifically exercise the "doesn't even look like the results screen"
    enrichment in _read_page_rows's failure path.
    """

    def __init__(self, monkeypatch, entries, game=None, results_screen_present=True):
        self.entries = entries
        self.calls = 0
        self.game = game
        monkeypatch.setattr(ocr, "read_page_indicator", self._read_page_indicator)
        monkeypatch.setattr(ocr, "read_page", self._read_page)
        monkeypatch.setattr(ocr, "is_augmentation_results_screen", lambda *a, **kw: results_screen_present)
        # read_full_roll now sleeps for real (settle delay + retry backoff)
        # -- stub it out so these stay fast, unit-level tests.
        monkeypatch.setattr(state_machine.time, "sleep", lambda seconds: None)

    def _page_index(self) -> int:
        if self.game is None:
            return 0
        return max(0, self.game.next_calls - self.game.prev_calls)

    def _current(self):
        idx = min(self._page_index(), len(self.entries) - 1)
        return self.entries[idx]

    def _read_page_indicator(self, screenshot, region_config):
        return self._current()[0]

    def _read_page(self, screenshot, template):
        page = self._current()[1]
        self.calls += 1
        return page

    def screenshot_fn(self):
        return _FAKE_SCREENSHOT


DUMMY_REGION_CONFIG = ocr.RegionConfig(
    page_indicator_box=(0, 0, 1, 1),
    results_title_box=(0, 0, 1, 1),
    row_templates={"single_page": [], "first_of_multi": [], "continuation": []},
    next_page_key="e",
    prev_page_key="q",
    window_title_hint="dummy",
)


# --- _describe_page / _confidence_tags: what the debug log actually
# shows for a page -- confirms the "how" tags (digit_source, debridge)
# appear for a real/unparseable row and are correctly absent for blank
# rows, which never read a digit at all. ---


def test_describe_page_shows_confidence_tags_for_a_real_gain(monkeypatch):
    row = ocr.ParsedRow(skill=SkillResult("Artillery", delta=1), blank=False, unparseable=False,
                         digit_source="template", debridge="none")
    assert state_machine._describe_page([row]) == "Artillery +1 [template]"


def test_describe_page_shows_debridge_tag_alongside_source(monkeypatch):
    row = ocr.ParsedRow(skill=SkillResult("Artillery", delta=1), blank=False, unparseable=False,
                         digit_source="tesseract:psm8", debridge="color")
    assert state_machine._describe_page([row]) == "Artillery +1 [tesseract:psm8, debridged:color]"


def test_describe_page_blank_row_has_no_confidence_tags(monkeypatch):
    assert state_machine._describe_page([_blank()]) == "blank"


def test_describe_page_unparseable_row_shows_tags_when_present(monkeypatch):
    row = ocr.ParsedRow(skill=None, blank=False, unparseable=True,
                         digit_source="sparkle-recovery", debridge="none")
    assert state_machine._describe_page([row]) == "UNPARSEABLE [sparkle-recovery]"


def test_single_page_no_indicator(monkeypatch):
    script = Script(monkeypatch, [
        (None, [_row("Artillery"), _row("Diversion"), _blank()]),
    ])
    game = FakeGame()
    results = state_machine.read_full_roll(
        script.screenshot_fn, game, DUMMY_REGION_CONFIG, DUMMY_WINDOW_BOUNDS, _goal(),
    )
    assert [r.name for r in results] == ["Artillery", "Diversion"]
    assert game.next_calls == 0
    assert game.prev_calls == 0


def test_two_page_roll_driven_by_indicator(monkeypatch):
    # protected_skills non-empty and reachable via page 1 gains, so the
    # "must check every page" path is exercised -- see the early-exit
    # tests below for the optimized paths.
    game = FakeGame()
    script = Script(monkeypatch, [
        ((1, 2), [_row("Artillery"), _row("Diversion"), _row("Critical Boost")]),
        ((2, 2), [_row("Partbreaker"), _blank(), _blank()]),
    ], game=game)
    results = state_machine.read_full_roll(
        script.screenshot_fn, game, DUMMY_REGION_CONFIG, DUMMY_WINDOW_BOUNDS,
        _goal(protected_skills=frozenset({"Blood Awakening"}), required="Artillery"),
    )
    assert [r.name for r in results] == [
        "Artillery", "Diversion", "Critical Boost", "Partbreaker",
    ]
    assert game.next_calls == 1  # driven by total=2, not by page content
    assert game.prev_calls == 1  # reset back to page 1


def test_three_page_roll(monkeypatch):
    game = FakeGame()
    script = Script(monkeypatch, [
        ((1, 3), [_row("Skill A"), _row("Skill B"), _row("Skill C")]),
        ((2, 3), [_row("Skill D"), _row("Skill E"), _row("Skill F")]),
        ((3, 3), [_row("Skill G"), _blank(), _blank()]),
    ], game=game)
    results = state_machine.read_full_roll(
        script.screenshot_fn, game, DUMMY_REGION_CONFIG, DUMMY_WINDOW_BOUNDS,
        _goal(protected_skills=frozenset({"Blood Awakening"}), required="Skill A"),
    )
    assert len(results) == 7
    assert game.next_calls == 2
    assert game.prev_calls == 2


# --- Early-exit optimization 1: gained/increased skills are capped at 3,
# exactly page 1's display capacity, so they never land on page 2+ (a roll
# touches at most 5 skills total -- confirmed by the user). Page 2+ can
# therefore only ever add more *removed* skills, which only matter if
# protected_skills is non-empty -- so page 2+ is skippable whenever either
# there's nothing protected to check, or a violation already turned up on
# an earlier page. ---


def test_no_protected_skills_skips_page2_entirely(monkeypatch):
    script = Script(monkeypatch, [
        ((1, 2), [_row("Artillery"), _row("Diversion"), _row("Critical Boost")]),
        ((2, 2), [_row("Partbreaker"), _blank(), _blank()]),  # never read
    ])
    game = FakeGame()
    results = state_machine.read_full_roll(
        script.screenshot_fn, game, DUMMY_REGION_CONFIG, DUMMY_WINDOW_BOUNDS,
        _goal(protected_skills=frozenset(), required="Artillery"),
    )
    assert [r.name for r in results] == ["Artillery", "Diversion", "Critical Boost"]
    assert game.next_calls == 0  # never paged forward
    assert game.prev_calls == 0
    assert script.calls == 1  # only page 1 was ever read


def test_protected_violation_on_page1_skips_page2(monkeypatch):
    script = Script(monkeypatch, [
        ((1, 2), [_row("Artillery"), _removed_row("Blood Awakening"), _row("Diversion")]),
        ((2, 2), [_row("Partbreaker"), _blank(), _blank()]),  # never read
    ])
    game = FakeGame()
    results = state_machine.read_full_roll(
        script.screenshot_fn, game, DUMMY_REGION_CONFIG, DUMMY_WINDOW_BOUNDS,
        _goal(protected_skills=frozenset({"Blood Awakening"}), required="Artillery"),
    )
    assert [r.name for r in results] == ["Artillery", "Blood Awakening", "Diversion"]
    assert game.next_calls == 0
    assert game.prev_calls == 0
    assert script.calls == 1


def test_protected_violation_on_page2_stops_there(monkeypatch):
    game = FakeGame()
    script = Script(monkeypatch, [
        ((1, 3), [_row("Artillery"), _row("Diversion"), _row("Critical Boost")]),
        ((2, 3), [_removed_row("Blood Awakening"), _blank(), _blank()]),
        ((3, 3), [_row("Skill Z"), _blank(), _blank()]),  # never read
    ], game=game)
    results = state_machine.read_full_roll(
        script.screenshot_fn, game, DUMMY_REGION_CONFIG, DUMMY_WINDOW_BOUNDS,
        _goal(protected_skills=frozenset({"Blood Awakening"}), required="Artillery"),
    )
    assert "Skill Z" not in [r.name for r in results]
    assert game.next_calls == 1  # paged forward once (to page 2), not twice (to page 3)
    assert game.prev_calls == 1  # reset matches exactly how far it went
    assert script.calls == 2


def test_protected_skill_loss_not_violating_because_not_present_reads_all_pages(monkeypatch):
    # protected_skills configured, no violation anywhere -- must still
    # read every page, same as before this optimization existed.
    game = FakeGame()
    script = Script(monkeypatch, [
        ((1, 2), [_row("Artillery"), _row("Diversion"), _row("Critical Boost")]),
        ((2, 2), [_row("Partbreaker"), _blank(), _blank()]),
    ], game=game)
    results = state_machine.read_full_roll(
        script.screenshot_fn, game, DUMMY_REGION_CONFIG, DUMMY_WINDOW_BOUNDS,
        _goal(protected_skills=frozenset({"Blood Awakening"}), required="Artillery"),  # never appears
    )
    assert [r.name for r in results] == [
        "Artillery", "Diversion", "Critical Boost", "Partbreaker",
    ]
    assert game.next_calls == 1
    assert game.prev_calls == 1


# --- Early-exit optimization 2: if page 1's gains already can't satisfy
# *any* profile's required_skills, the roll is doomed no matter what page
# 2+ contains (page 2+ can only add removals, never new gains) -- so it's
# skipped even with protected_skills configured, since checking for a
# protected-skill violation there would be answering a question whose
# answer no longer matters. ---


def test_page1_not_satisfying_any_profile_skips_page2_even_with_protection_configured(monkeypatch):
    script = Script(monkeypatch, [
        ((1, 2), [_row("Agitator"), _row("Diversion"), _row("Critical Boost")]),
        ((2, 2), [_removed_row("Blood Awakening"), _blank(), _blank()]),  # never read
    ])
    game = FakeGame()
    results = state_machine.read_full_roll(
        script.screenshot_fn, game, DUMMY_REGION_CONFIG, DUMMY_WINDOW_BOUNDS,
        _goal(protected_skills=frozenset({"Blood Awakening"}), required="Mail of Hellfire"),
    )
    assert [r.name for r in results] == ["Agitator", "Diversion", "Critical Boost"]
    assert game.next_calls == 0
    assert game.prev_calls == 0
    assert script.calls == 1


# --- Early-exit optimization 3 (within a single page): once a protected
# skill's loss is confirmed among rows that DID parse, stop retrying rows
# that are still unparseable on the same page -- the roll is already
# rejected regardless of what they'd have said. ---


def test_confirmed_violation_short_circuits_retries_for_other_unparseable_rows(monkeypatch):
    # Pierce Up and Tune-Up both sparkle-obscured (unparseable), but
    # Blood Awakening's loss is clearly readable -- shouldn't need any
    # retries to know the answer.
    script = Script(monkeypatch, [
        (None, [_unparseable(), _unparseable(), _removed_row("Blood Awakening")]),
    ])
    game = FakeGame()
    results = state_machine.read_full_roll(
        script.screenshot_fn, game, DUMMY_REGION_CONFIG, DUMMY_WINDOW_BOUNDS,
        _goal(protected_skills=frozenset({"Blood Awakening"})),
    )
    assert [r.name for r in results] == ["Blood Awakening"]
    assert script.calls == 1  # no retries triggered


def test_unparseable_row_raises_after_exhausting_retries(monkeypatch):
    # Default results_screen_present=True -- confirms the generic message
    # still fires (not the wrong-screen one) when the screen genuinely is
    # the results screen and it's just a row that won't parse.
    script = Script(monkeypatch, [
        (None, [_row("Artillery"), _unparseable(), _blank()]),
    ])
    game = FakeGame()
    try:
        state_machine.read_full_roll(
            script.screenshot_fn, game, DUMMY_REGION_CONFIG, DUMMY_WINDOW_BOUNDS, _goal(),
        )
        assert False, "expected UnreadableRollError"
    except state_machine.UnreadableRollError as e:
        assert "doesn't even look like" not in str(e)
    # 1 initial read + UNREADABLE_RETRY_COUNT retries, all re-capturing
    assert script.calls == 1 + state_machine.UNREADABLE_RETRY_COUNT


def test_unparseable_row_on_wrong_screen_gives_specific_error(monkeypatch):
    # Confirmed live: a saved failure screenshot turned out to be STATE3
    # ("Requires materials... Proceed?"), not STATE4 at all -- reading
    # skill rows off a screen that has none produced a generic "couldn't
    # be parsed" error with no indication of the real problem.
    script = Script(monkeypatch, [
        (None, [_unparseable(), _unparseable(), _unparseable()]),
    ], results_screen_present=False)
    game = FakeGame()
    try:
        state_machine.read_full_roll(
            script.screenshot_fn, game, DUMMY_REGION_CONFIG, DUMMY_WINDOW_BOUNDS, _goal(),
        )
        assert False, "expected UnreadableRollError"
    except state_machine.UnreadableRollError as e:
        assert "doesn't even look like the Augmentation Results screen" in str(e)
        assert "out of materials" in str(e)


def test_unparseable_row_recovers_on_retry(monkeypatch):
    # Simulates a sparkle decoration clearing between captures: the same
    # slot reads unparseable, then cleanly on the next capture, without
    # ever raising.
    call_count = {"n": 0}

    def flaky_read_page(screenshot, template):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return [_row("Artillery"), _unparseable(), _blank()]
        return [_row("Artillery"), _row("Diversion"), _blank()]

    monkeypatch.setattr(ocr, "read_page", flaky_read_page)
    monkeypatch.setattr(ocr, "read_page_indicator", lambda screenshot, region_config: None)
    monkeypatch.setattr(state_machine.time, "sleep", lambda seconds: None)

    game = FakeGame()
    results = state_machine.read_full_roll(
        lambda: _FAKE_SCREENSHOT, game, DUMMY_REGION_CONFIG, DUMMY_WINDOW_BOUNDS, _goal(),
    )
    assert [r.name for r in results] == ["Artillery", "Diversion"]
    assert call_count["n"] == 2  # one failed read, one retry that succeeded


def test_page_transition_that_never_lands_raises_clear_error(monkeypatch):
    # next_page() gets pressed, but the on-screen indicator never actually
    # advances past page 1 -- only one entry in the script, so _current()
    # keeps returning it regardless of how many times next_page() is
    # called, simulating a page-turn keypress that didn't register (or
    # whose transition never completed). Confirmed live: this previously
    # fell through to a "continuation" read against real page-1 content
    # (Slots/Water Resistance rows misread as garbled/blank skill rows),
    # raising a confusing OCR-failure error instead of naming the actual
    # problem -- see _read_page_rows's expected_page handling.
    game = FakeGame()
    script = Script(monkeypatch, [
        ((1, 2), [_row("Artillery"), _row("Diversion"), _row("Critical Boost")]),
    ], game=game)
    try:
        state_machine.read_full_roll(
            script.screenshot_fn, game, DUMMY_REGION_CONFIG, DUMMY_WINDOW_BOUNDS,
            _goal(protected_skills=frozenset({"Blood Awakening"}), required="Artillery"),
        )
        assert False, "expected UnreadableRollError"
    except state_machine.UnreadableRollError as e:
        assert "page 2" in str(e)
        assert "1/2" in str(e)
    assert game.next_calls == 1  # tried to turn the page once, then gave up
    assert game.prev_calls == 1  # and the cleanup still ran despite the raise -- see next test


def test_page_indicator_reverting_mid_retry_is_caught(monkeypatch):
    # The gap a *one-shot* pre-check (checked once, before handing off to
    # a retry loop that never re-checks) would miss: the indicator
    # correctly shows page 2 on the very first capture, but the row
    # content on that same capture is unparseable, triggering a retry --
    # and on that retry, the indicator has reverted to page 1. Every
    # capture within _read_page_rows must re-verify the indicator, not
    # just the first one, or this reversion reads as a generic
    # unparseable-row failure instead of the real "wrong page" problem.
    calls = {"n": 0}

    def flaky_indicator(screenshot, region_config):
        calls["n"] += 1
        return (2, 2) if calls["n"] == 1 else (1, 2)

    monkeypatch.setattr(ocr, "read_page_indicator", flaky_indicator)
    monkeypatch.setattr(ocr, "read_page", lambda screenshot, template: [_unparseable(), _blank(), _blank()])
    monkeypatch.setattr(state_machine.time, "sleep", lambda seconds: None)

    game = FakeGame()
    try:
        state_machine._read_page_rows(
            lambda: _FAKE_SCREENSHOT, [], game, DUMMY_WINDOW_BOUNDS,
            frozenset(), lambda: False,
            region_config=DUMMY_REGION_CONFIG, expected_page=2,
        )
        assert False, "expected UnreadableRollError"
    except state_machine.UnreadableRollError as e:
        assert "page 2" in str(e)
        assert "1/2" in str(e)


def test_page_read_failure_still_navigates_back_to_page_1(monkeypatch):
    # A raise out of the page 2+ read must not abandon the game mid-
    # pagination: next_page_presses was already incremented before the
    # failing read, so without a try/finally around it, an exception here
    # skips the prev_page() cleanup entirely -- the game is left sitting
    # on page 2, and the *next* invocation's trigger_roll() macro (which
    # assumes it's starting from Material Select, STATE1) sends the wrong
    # keys into a still-on-page-2 Augmentation Results screen instead.
    # That's a plausible way one crash's leftover state corrupts the next
    # run's very first read -- confirmed as a real gap in this code, not
    # just theorized.
    game = FakeGame()
    script = Script(monkeypatch, [
        ((1, 3), [_row("Artillery"), _row("Diversion"), _row("Critical Boost")]),
        ((2, 3), [_unparseable(), _blank(), _blank()]),  # never recovers -- exhausts retries
    ], game=game)
    try:
        state_machine.read_full_roll(
            script.screenshot_fn, game, DUMMY_REGION_CONFIG, DUMMY_WINDOW_BOUNDS,
            _goal(protected_skills=frozenset({"Blood Awakening"}), required="Artillery"),
        )
        assert False, "expected UnreadableRollError"
    except state_machine.UnreadableRollError:
        pass
    assert game.next_calls == game.prev_calls == 1


def test_indicator_not_starting_on_page_1_raises(monkeypatch):
    script = Script(monkeypatch, [
        ((2, 2), [_row("Artillery"), _blank(), _blank()]),
    ])
    game = FakeGame()
    try:
        state_machine.read_full_roll(
            script.screenshot_fn, game, DUMMY_REGION_CONFIG, DUMMY_WINDOW_BOUNDS, _goal(),
        )
        assert False, "expected UnreadableRollError"
    except state_machine.UnreadableRollError:
        pass


def test_implausible_page_count_raises(monkeypatch):
    script = Script(monkeypatch, [
        ((1, 99), [_row("Artillery"), _blank(), _blank()]),
    ])
    game = FakeGame()
    try:
        state_machine.read_full_roll(
            script.screenshot_fn, game, DUMMY_REGION_CONFIG, DUMMY_WINDOW_BOUNDS, _goal(),
        )
        assert False, "expected UnreadableRollError"
    except state_machine.UnreadableRollError:
        pass


# --- The initial page-indicator read (before any row content is looked
# at) is itself retried when the indicator box has content that looks
# like a real indicator but didn't parse -- see
# ocr.indicator_region_ambiguous / state_machine._detect_page_indicator.
# Without this, a single glow-corrupted frame permanently commits the
# whole attempt to the "single_page" template with no way to recover,
# even though 8 rounds of content retries happen afterward -- confirmed
# live, a genuine two-page roll's row0 landed on the "Skills" section
# header because single_page's rows sit at a different position than
# first_of_multi's. ---


def test_ambiguous_indicator_is_retried_and_recovers(monkeypatch):
    # First capture: indicator box has real (glow-corrupted) content that
    # fails to parse. A fresh capture a moment later reads it cleanly --
    # confirms the retry recovers the correct page count instead of
    # permanently committing to single_page after one bad frame.
    calls = {"n": 0}

    def flaky_indicator(screenshot, region_config):
        calls["n"] += 1
        return None if calls["n"] == 1 else (1, 2)

    monkeypatch.setattr(ocr, "read_page_indicator", flaky_indicator)
    monkeypatch.setattr(ocr, "indicator_region_ambiguous", lambda screenshot, region_config: True)
    monkeypatch.setattr(
        ocr, "read_page",
        lambda screenshot, template: [_row("Artillery"), _row("Diversion"), _row("Critical Boost")],
    )
    monkeypatch.setattr(state_machine.time, "sleep", lambda seconds: None)

    game = FakeGame()
    results = state_machine.read_full_roll(
        lambda: _FAKE_SCREENSHOT, game, DUMMY_REGION_CONFIG, DUMMY_WINDOW_BOUNDS, _goal(),
    )
    assert [r.name for r in results] == ["Artillery", "Diversion", "Critical Boost"]
    assert calls["n"] == 2  # one failed indicator read, one retry that succeeded


def test_non_ambiguous_missing_indicator_is_trusted_immediately(monkeypatch):
    # A genuinely single-page roll's indicator box either has nothing in
    # it, or (per ocr.indicator_region_ambiguous) only a narrow sliver of
    # an adjacent row bleeding in -- shouldn't pay any retry cost just to
    # rule out the ambiguous-multipage case that doesn't apply here.
    calls = {"n": 0}

    def indicator_fn(screenshot, region_config):
        calls["n"] += 1
        return None

    monkeypatch.setattr(ocr, "read_page_indicator", indicator_fn)
    monkeypatch.setattr(ocr, "indicator_region_ambiguous", lambda screenshot, region_config: False)
    monkeypatch.setattr(
        ocr, "read_page", lambda screenshot, template: [_row("Artillery"), _row("Diversion"), _blank()],
    )
    monkeypatch.setattr(state_machine.time, "sleep", lambda seconds: None)

    game = FakeGame()
    results = state_machine.read_full_roll(
        lambda: _FAKE_SCREENSHOT, game, DUMMY_REGION_CONFIG, DUMMY_WINDOW_BOUNDS, _goal(),
    )
    assert [r.name for r in results] == ["Artillery", "Diversion"]
    assert calls["n"] == 1  # no retries triggered


def test_ambiguous_indicator_that_never_resolves_falls_back_to_single_page(monkeypatch):
    # If the indicator box stays ambiguous through every retry, this
    # isn't a new hard-failure mode -- it falls back to the same
    # single_page attempt as before this fix existed, which then lives
    # or dies on its own (normal unparseable-row handling), rather than
    # raising its own separate error.
    monkeypatch.setattr(ocr, "read_page_indicator", lambda screenshot, region_config: None)
    monkeypatch.setattr(ocr, "indicator_region_ambiguous", lambda screenshot, region_config: True)
    monkeypatch.setattr(
        ocr, "read_page", lambda screenshot, template: [_row("Artillery"), _row("Diversion"), _blank()],
    )
    monkeypatch.setattr(state_machine.time, "sleep", lambda seconds: None)

    game = FakeGame()
    results = state_machine.read_full_roll(
        lambda: _FAKE_SCREENSHOT, game, DUMMY_REGION_CONFIG, DUMMY_WINDOW_BOUNDS, _goal(),
    )
    assert [r.name for r in results] == ["Artillery", "Diversion"]


# --- Force-stop hotkey support (see hotkeys.py): should_stop is checked
# during every wait via _interruptible_sleep, and read_full_roll's own
# settle-delay wait respects it too. ---


def test_interruptible_sleep_raises_immediately_when_already_stopped(monkeypatch):
    start = time.monotonic()
    try:
        state_machine._interruptible_sleep(10.0, lambda: True)
        assert False, "expected StopRequested"
    except state_machine.StopRequested:
        pass
    assert time.monotonic() - start < 0.1  # didn't wait anywhere near the full 10s


def test_interruptible_sleep_completes_normally_when_never_stopped(monkeypatch):
    start = time.monotonic()
    state_machine._interruptible_sleep(0.05, lambda: False)
    assert time.monotonic() - start >= 0.04


def test_interruptible_sleep_stops_partway_through(monkeypatch):
    # Flips to "stop" after a short real delay -- confirms it's actually
    # polling during the wait, not just checking once at the start.
    deadline = time.monotonic() + 0.05

    def should_stop():
        return time.monotonic() >= deadline

    start = time.monotonic()
    try:
        state_machine._interruptible_sleep(10.0, should_stop)
        assert False, "expected StopRequested"
    except state_machine.StopRequested:
        pass
    elapsed = time.monotonic() - start
    assert 0.04 <= elapsed < 1.0  # stopped soon after the deadline, not after the full 10s


def test_read_full_roll_propagates_stop_requested(monkeypatch):
    # should_stop is already true before the settle-delay wait even
    # starts -- read_full_roll should never get as far as capturing a
    # screenshot.
    script = Script(monkeypatch, [
        (None, [_row("Artillery"), _row("Diversion"), _blank()]),
    ])
    game = FakeGame()
    try:
        state_machine.read_full_roll(
            script.screenshot_fn, game, DUMMY_REGION_CONFIG, DUMMY_WINDOW_BOUNDS, _goal(),
            should_stop=lambda: True,
        )
        assert False, "expected StopRequested"
    except state_machine.StopRequested:
        pass
    assert script.calls == 0  # never got as far as reading a page


# --- run(): the full autonomous loop, previously entirely uncovered (it
# needs a real game window otherwise) -- monkeypatches every boundary
# (window capture, GameInput, evaluate, AttemptLogger's log_dir) to drive
# it fully offline. Focused on the new progress-readout behavior itself,
# not re-testing read_full_roll's pagination logic (covered above). ---

class _FakeTTYStdout(io.StringIO):
    """A real terminal always has isatty() True -- run()'s in-place
    progress readout only kicks in then, so this is needed to actually
    exercise that path under pytest, which otherwise captures stdout as a
    non-tty pipe."""

    def isatty(self):
        return True


def test_run_uses_progress_readout_for_boring_attempts_and_full_lines_for_notable_ones(monkeypatch):
    game = FakeGame()
    script = Script(monkeypatch, [
        (None, [_row("Artillery"), _blank(), _blank()]),
    ], game=game)

    monkeypatch.setattr(state_machine.capture, "find_game_window", lambda hint: SimpleNamespace(bounds=DUMMY_WINDOW_BOUNDS))
    monkeypatch.setattr(state_machine.capture, "screenshot_window", lambda window: script.screenshot_fn())
    monkeypatch.setattr(state_machine, "GameInput", lambda **kwargs: game)

    original_logger_cls = state_machine.AttemptLogger
    tmpdir = tempfile.TemporaryDirectory()
    monkeypatch.setattr(
        state_machine, "AttemptLogger",
        lambda goal: original_logger_cls(goal=goal, log_dir=Path(tmpdir.name)),
    )

    # attempt 1: boring reject -- should collapse to the in-place progress
    # line, no per-attempt detail printed. attempt 2: rejected but with a
    # suspiciously large delta -- must break through with its full line
    # even though it isn't accepted. attempt 3: accepted -- also a full
    # line, and ends the loop.
    decisions = iter([
        Decision(False, "goal not met", [SkillResult("Artillery", delta=1)], []),
        Decision(False, "goal not met", [SkillResult("Weird Skill", delta=9)], []),
        Decision(True, "matched profile", [SkillResult("Artillery", delta=1)], []),
    ])
    monkeypatch.setattr(state_machine, "evaluate", lambda goal, roll: next(decisions))

    fake_out = _FakeTTYStdout()
    monkeypatch.setattr(sys, "stdout", fake_out)
    try:
        result = state_machine.run(_goal(), region_config=DUMMY_REGION_CONFIG, max_attempts=5)
    finally:
        tmpdir.cleanup()

    output = fake_out.getvalue()
    assert result.accepted
    assert result.attempts == 3
    assert game.trigger_calls == 1
    assert game.accept_calls == 1
    assert game.reroll_calls == 2  # attempts 1 and 2 both reject (2 is notable, but still rerolled)

    assert "attempt 1/5" in output  # boring attempt 1's in-place progress line
    assert "[attempt #1]" not in output  # ...and never its full log line
    assert "[attempt #2]" in output  # suspicious attempt 2 breaks through
    assert "[?!] UNUSUALLY LARGE DELTA" in output
    assert "[attempt #3]" in output  # accepted attempt 3
    assert "ACCEPTED" in output


def test_run_prints_every_full_line_when_stdout_is_not_a_tty(monkeypatch):
    # Redirected to a file/pipe -- an in-place \r update would just be
    # unreadable noise, so every attempt (even a boring one) prints its
    # full line instead, matching the pre-progress-readout behavior
    # exactly for this case.
    game = FakeGame()
    script = Script(monkeypatch, [
        (None, [_row("Artillery"), _blank(), _blank()]),
    ], game=game)

    monkeypatch.setattr(state_machine.capture, "find_game_window", lambda hint: SimpleNamespace(bounds=DUMMY_WINDOW_BOUNDS))
    monkeypatch.setattr(state_machine.capture, "screenshot_window", lambda window: script.screenshot_fn())
    monkeypatch.setattr(state_machine, "GameInput", lambda **kwargs: game)

    original_logger_cls = state_machine.AttemptLogger
    tmpdir = tempfile.TemporaryDirectory()
    monkeypatch.setattr(
        state_machine, "AttemptLogger",
        lambda goal: original_logger_cls(goal=goal, log_dir=Path(tmpdir.name)),
    )

    decisions = iter([
        Decision(False, "goal not met", [SkillResult("Artillery", delta=1)], []),
        Decision(True, "matched profile", [SkillResult("Artillery", delta=1)], []),
    ])
    monkeypatch.setattr(state_machine, "evaluate", lambda goal, roll: next(decisions))

    fake_out = io.StringIO()  # isatty() is False by default
    monkeypatch.setattr(sys, "stdout", fake_out)
    try:
        result = state_machine.run(_goal(), region_config=DUMMY_REGION_CONFIG, max_attempts=5)
    finally:
        tmpdir.cleanup()

    output = fake_out.getvalue()
    assert result.accepted
    assert "[attempt #1]" in output  # boring attempt still prints its full line
    assert "[attempt #2]" in output
    assert "attempt 1/5" not in output  # progress readout never used


# --- _format_progress: the live in-place progress readout run() prints for
# a "boring" (non-accepted, non-suspicious) attempt on a long run, instead
# of one full decision line scrolling past per attempt. Pure formatting,
# no game/OCR dependencies. ---

def test_format_progress_shows_attempt_rate_and_elapsed(monkeypatch):
    line = state_machine._format_progress(30, 300, elapsed=60.0)
    assert "attempt 30/300" in line
    assert "30.0/min" in line
    assert "elapsed 1m00s" in line


def test_format_progress_handles_zero_elapsed(monkeypatch):
    # Guards the division-by-zero edge case at the very first attempt,
    # before any real time has passed yet.
    line = state_machine._format_progress(1, 300, elapsed=0.0)
    assert "0.0/min" in line
    assert "elapsed 0m00s" in line


def test_format_progress_padded_to_fixed_width(monkeypatch):
    # Padded so an in-place \r update always fully overwrites a longer
    # previous line (e.g. the moment attempt count gains a digit) --
    # never leaves a trailing fragment of it on screen.
    short = state_machine._format_progress(9, 300, elapsed=5.0)
    long = state_machine._format_progress(299, 300, elapsed=5000.0)
    assert len(short) == len(long) == state_machine._PROGRESS_LINE_WIDTH


def run_all():
    class FakeMonkeypatch:
        def __init__(self):
            self._undo = []

        def setattr(self, obj, name, value):
            self._undo.append((obj, name, getattr(obj, name)))
            setattr(obj, name, value)

        def undo(self):
            for obj, name, value in self._undo:
                setattr(obj, name, value)

    tests = [(k, v) for k, v in globals().items() if k.startswith("test_")]
    failures = 0
    for name, t in tests:
        mp = FakeMonkeypatch()
        try:
            t(mp)
            print(f"PASS {name}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL {name}: {e}")
        finally:
            mp.undo()
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    run_all()
