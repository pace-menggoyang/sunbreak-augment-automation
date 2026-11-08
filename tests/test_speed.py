"""Offline tests for the speed profiles (speed.py) and the pieces that act on
them: GameInput's slowdown ceiling / set_timing, run()'s revert-to-normal
guard, and main.py's menu / CLI wiring."""
import io
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qurio_aug import main, speed, state_machine
from qurio_aug.decision import Decision, SkillResult
from qurio_aug.input import POST_PRESS_DELAY, PRESS_HOLD, GameInput


class _FakeController:
    def press(self, key):
        pass

    def release(self, key):
        pass


class _FakeMouse:
    position = None


# --- the profiles themselves ---


def test_normal_profile_is_exactly_the_tested_defaults():
    assert speed.NORMAL.timings == (PRESS_HOLD, POST_PRESS_DELAY, state_machine.RESULT_SETTLE_DELAY)
    assert not speed.NORMAL.experimental


def test_safe_profile_only_shortens_the_settle_wait():
    # Must not touch key timing -- that is the whole reason it is "safe".
    assert speed.SAFE.press_hold == speed.NORMAL.press_hold
    assert speed.SAFE.post_press_delay == speed.NORMAL.post_press_delay
    assert speed.SAFE.settle_delay < speed.NORMAL.settle_delay
    assert not speed.SAFE.experimental


def test_fast_profile_is_experimental_and_never_shortens_the_key_hold():
    assert speed.FAST.experimental
    assert speed.FAST.post_press_delay < speed.NORMAL.post_press_delay
    assert speed.FAST.settle_delay <= speed.SAFE.settle_delay
    # Nothing below 30ms was ever measured, so no profile goes there.
    assert speed.FAST.press_hold == speed.NORMAL.press_hold


def test_get_profile_falls_back_to_normal():
    assert speed.get_profile("fast") is speed.FAST
    assert speed.get_profile("nonsense") is speed.NORMAL
    assert speed.get_profile(None) is speed.NORMAL


# --- GameInput: the slowdown ceiling and set_timing ---


def test_slow_down_climbs_back_to_the_ceiling_but_not_past_it():
    game = GameInput(controller=_FakeController(), post_press_delay=0.12, mouse=_FakeMouse(),
                     max_post_press_delay=POST_PRESS_DELAY)
    while game.slow_down() is not None:
        pass
    assert abs(game.post_press_delay - POST_PRESS_DELAY) < 1e-9  # the normal delay, not 2x the fast one


def test_set_timing_replaces_the_delays_and_resets_the_ceiling():
    game = GameInput(controller=_FakeController(), post_press_delay=0.12, mouse=_FakeMouse(),
                     max_post_press_delay=POST_PRESS_DELAY)
    game.set_timing(PRESS_HOLD, POST_PRESS_DELAY)
    assert game.post_press_delay == POST_PRESS_DELAY
    # Back to the default cap (2x the new base), no longer the fast run's ceiling.
    while game.slow_down() is not None:
        pass
    assert abs(game.post_press_delay - POST_PRESS_DELAY * 2) < 1e-9


# --- run(): reverts to normal after repeated stray dialogs ---


def test_run_reverts_to_normal_after_repeated_dialogs():
    class Game:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.timing_calls = []
            self.macros = 0

        def park_mouse(self, bounds):
            pass

        def trigger_roll(self):
            pass

        def accept_macro(self):
            pass

        def reroll_macro(self):
            self.macros += 1

        def set_timing(self, hold, delay):
            self.timing_calls.append((hold, delay))

    game_holder = {}

    def make_game(**kwargs):
        game_holder["game"] = Game(**kwargs)
        return game_holder["game"]

    reads = {"n": 0}

    def fake_recovering(read, game, debug_log, stats=None):
        reads["n"] += 1
        stats.dialogs["apply_results"] += 2  # two stray dialogs per attempt
        return [SkillResult("Artillery", delta=1)]

    originals = (
        state_machine.capture.find_game_window, state_machine.GameInput,
        state_machine._read_recovering_from_dialogs, state_machine.evaluate,
        state_machine.AttemptLogger,
    )
    tmpdir = tempfile.TemporaryDirectory()
    original_logger_cls = state_machine.AttemptLogger
    state_machine.capture.find_game_window = lambda hint: SimpleNamespace(bounds=(0, 0, 100, 100))
    state_machine.GameInput = make_game
    state_machine._read_recovering_from_dialogs = fake_recovering
    state_machine.evaluate = lambda goal, roll: Decision(False, "no", [SkillResult("Artillery", delta=1)], [])
    state_machine.AttemptLogger = lambda goal: original_logger_cls(goal=goal, log_dir=Path(tmpdir.name))
    out = io.StringIO()
    real_stdout, sys.stdout = sys.stdout, out
    try:
        from qurio_aug import ocr
        cfg = ocr.RegionConfig(
            page_indicator_box=(0, 0, 1, 1), results_title_box=(0, 0, 1, 1),
            row_templates={"single_page": [], "first_of_multi": [], "continuation": []},
            next_page_key="e", prev_page_key="q", window_title_hint="x",
        )
        from qurio_aug.decision import Goal, Profile
        goal = Goal(name="t", augment_type="skills_plus", profiles=(Profile(required_skills=()),),
                    protected_skills=frozenset())
        # read_full_roll is never reached (the recovering wrapper is faked), so no OCR is touched.
        state_machine.run(
            goal, region_config=cfg, max_attempts=3, press_hold=0.03, post_press_delay=0.12,
            settle_delay=0.2, speed_label="Fast (experimental)",
            fallback_timing=(PRESS_HOLD, POST_PRESS_DELAY, state_machine.RESULT_SETTLE_DELAY),
        )
    finally:
        sys.stdout = real_stdout
        (state_machine.capture.find_game_window, state_machine.GameInput,
         state_machine._read_recovering_from_dialogs, state_machine.evaluate,
         state_machine.AttemptLogger) = originals
        tmpdir.cleanup()

    game = game_holder["game"]
    # 2 dialogs after attempt 1, 4 after attempt 2 -> reverts once, on attempt 2 (>= 3).
    assert game.timing_calls == [(PRESS_HOLD, POST_PRESS_DELAY)]
    assert game.kwargs["max_post_press_delay"] == POST_PRESS_DELAY  # ceiling = the normal delay
    output = out.getvalue()
    assert "fast mode switched off after 4 stray dialogs" in output
    assert "switched back to Normal" in output  # and in the run summary


# --- main.py: menu, status line, dispatch, _execute ---


def test_speed_is_a_menu_item_and_a_known_command():
    assert "speed" in [cmd_id for cmd_id, _ in main._MENU_ITEMS]
    assert "speed" in {name for names, _ in main._COMMANDS for name in names}


def test_status_shows_the_speed_and_flags_experimental_as_a_warning():
    assert main._classify_status_line("speed: Normal") == "neutral"
    assert main._classify_status_line("speed: Faster") == "neutral"
    assert main._classify_status_line("speed: Fast (experimental)") == "warn"


def test_dispatch_speed_shortcut_sets_the_session_speed():
    session = main._SessionState(region_config=None)
    assert main._dispatch_command("speed", "fast", session) is True
    assert session.speed == "fast"
    main._dispatch_command("speed", "normal", session)
    assert session.speed == "normal"


def test_execute_hands_a_fast_profile_to_run_with_the_normal_timings_as_fallback():
    captured = {}
    original = state_machine.run

    def fake_run(goal, **kwargs):
        captured.update(kwargs)
        return state_machine.RunResult(True, 1, None)

    state_machine.run = fake_run
    real_stdout, sys.stdout = sys.stdout, io.StringIO()
    try:
        main._execute(object(), False, 5, lambda: False, speed=speed.FAST, press_hold=0.03,
                      post_press_delay=0.12, settle_delay=0.2)
    finally:
        sys.stdout = real_stdout
        state_machine.run = original
    assert captured["speed_label"] == speed.FAST.label
    assert captured["fallback_timing"] == speed.NORMAL.timings
    assert "speed" not in captured  # the profile itself is split out, run() takes label + fallback


def test_execute_with_normal_speed_passes_no_fallback():
    captured = {}
    original = state_machine.run

    def fake_run(goal, **kwargs):
        captured.update(kwargs)
        return state_machine.RunResult(True, 1, None)

    state_machine.run = fake_run
    real_stdout, sys.stdout = sys.stdout, io.StringIO()
    try:
        main._execute(object(), False, 5, lambda: False, speed=speed.NORMAL, press_hold=0.03,
                      post_press_delay=0.18, settle_delay=0.5)
    finally:
        sys.stdout = real_stdout
        state_machine.run = original
    assert "fallback_timing" not in captured and "speed_label" not in captured


def run_all():
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}", file=sys.__stdout__)
        except AssertionError as e:
            failures += 1
            print(f"FAIL {t.__name__}: {e}", file=sys.__stdout__)
    print(f"\n{len(tests) - failures}/{len(tests)} passed", file=sys.__stdout__)
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    run_all()
