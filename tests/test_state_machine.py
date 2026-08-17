"""Offline tests for the page-indicator-driven pagination logic in
state_machine.read_full_roll, using monkeypatched ocr.read_page /
ocr.read_page_indicator so no real screenshots/OCR/game are needed.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image

from qurio_aug import ocr, state_machine
from qurio_aug.decision import Goal, Profile, RequiredSkill, SkillResult

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

    def next_page(self):
        self.next_calls += 1

    def prev_page(self):
        self.prev_calls += 1

    def park_mouse(self, window_bounds):
        self.park_calls += 1


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
    """

    def __init__(self, monkeypatch, entries, game=None):
        self.entries = entries
        self.calls = 0
        self.game = game
        monkeypatch.setattr(ocr, "read_page_indicator", self._read_page_indicator)
        monkeypatch.setattr(ocr, "read_page", self._read_page)
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
    row_templates={"single_page": [], "first_of_multi": [], "continuation": []},
    next_page_key="e",
    prev_page_key="q",
    window_title_hint="dummy",
)


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
    script = Script(monkeypatch, [
        (None, [_row("Artillery"), _unparseable(), _blank()]),
    ])
    game = FakeGame()
    try:
        state_machine.read_full_roll(
            script.screenshot_fn, game, DUMMY_REGION_CONFIG, DUMMY_WINDOW_BOUNDS, _goal(),
        )
        assert False, "expected UnreadableRollError"
    except state_machine.UnreadableRollError:
        pass
    # 1 initial read + UNREADABLE_RETRY_COUNT retries, all re-capturing
    assert script.calls == 1 + state_machine.UNREADABLE_RETRY_COUNT


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
