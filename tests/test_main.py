"""Offline tests for main.py's pure logic -- _execute's exit-code mapping
and _select_goal_path's number/path parsing -- using monkeypatched
state_machine calls and a temp directory so no real game/OCR/hotkeys or
the project's actual goals/ contents are needed.
"""
import builtins
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qurio_aug import calibrate, capture, main, ocr, state_machine
from qurio_aug.decision import Decision, Goal, Profile


def _goal() -> Goal:
    return Goal(name="test", augment_type="skills_plus", profiles=(Profile(required_skills=()),))


# --- _execute: dry-run branch (state_machine.evaluate_current_screen) ---


def test_execute_dry_run_accepted_returns_0(monkeypatch):
    monkeypatch.setattr(
        state_machine, "evaluate_current_screen",
        lambda goal, **kw: Decision(True, "matched", [], []),
    )
    assert main._execute(_goal(), dry_run=True, max_attempts=10, should_stop=lambda: False) == 0


def test_execute_dry_run_rejected_returns_1(monkeypatch):
    monkeypatch.setattr(
        state_machine, "evaluate_current_screen",
        lambda goal, **kw: Decision(False, "no match", [], []),
    )
    assert main._execute(_goal(), dry_run=True, max_attempts=10, should_stop=lambda: False) == 1


def test_execute_dry_run_unreadable_returns_2(monkeypatch):
    def boom(goal, **kw):
        raise state_machine.UnreadableRollError("simulated")

    monkeypatch.setattr(state_machine, "evaluate_current_screen", boom)
    assert main._execute(_goal(), dry_run=True, max_attempts=10, should_stop=lambda: False) == 2


# --- _execute: full-run branch (state_machine.run) ---


def test_execute_full_run_accepted_returns_0(monkeypatch):
    monkeypatch.setattr(
        state_machine, "run",
        lambda goal, **kw: state_machine.RunResult(True, 5, Decision(True, "matched", [], [])),
    )
    assert main._execute(_goal(), dry_run=False, max_attempts=10, should_stop=lambda: False) == 0


def test_execute_full_run_gave_up_returns_1(monkeypatch):
    monkeypatch.setattr(
        state_machine, "run",
        lambda goal, **kw: state_machine.RunResult(False, 10, None),
    )
    assert main._execute(_goal(), dry_run=False, max_attempts=10, should_stop=lambda: False) == 1


def test_execute_full_run_stopped_returns_130(monkeypatch):
    monkeypatch.setattr(
        state_machine, "run",
        lambda goal, **kw: state_machine.RunResult(False, 3, None, stopped=True),
    )
    assert main._execute(_goal(), dry_run=False, max_attempts=10, should_stop=lambda: False) == 130


def test_execute_full_run_unreadable_returns_2(monkeypatch):
    def boom(goal, **kw):
        raise state_machine.UnreadableRollError("simulated")

    monkeypatch.setattr(state_machine, "run", boom)
    assert main._execute(_goal(), dry_run=False, max_attempts=10, should_stop=lambda: False) == 2


# --- _run_with_hotkeys: pressing the stop hotkey *before* start cancels
# back to the caller (return 130, _execute never runs) instead of the
# only way out being to quit the whole app -- previously untested (needs
# a real HotkeyController otherwise, which starts a real pynput
# listener). ---


class _FakeHotkeys:
    def __init__(self, start_result: bool):
        self._start_result = start_result

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def wait_for_start(self) -> bool:
        return self._start_result

    def stop_requested(self) -> bool:
        return False


def test_run_with_hotkeys_cancels_before_start_without_executing(monkeypatch):
    monkeypatch.setattr(main, "HotkeyController", lambda *a, **kw: _FakeHotkeys(start_result=False))

    def boom(*a, **kw):
        raise AssertionError("should not execute when cancelled before start")

    monkeypatch.setattr(main, "_execute", boom)
    result = main._run_with_hotkeys(
        _goal(), dry_run=False, max_attempts=10, common_kwargs={},
        start_hotkey="<alt>+m", stop_hotkey="<alt>+n",
    )
    assert result == 130


def test_run_with_hotkeys_executes_when_start_fires(monkeypatch):
    monkeypatch.setattr(main, "HotkeyController", lambda *a, **kw: _FakeHotkeys(start_result=True))
    monkeypatch.setattr(main, "_execute", lambda *a, **kw: 0)
    result = main._run_with_hotkeys(
        _goal(), dry_run=False, max_attempts=10, common_kwargs={},
        start_hotkey="<alt>+m", stop_hotkey="<alt>+n",
    )
    assert result == 0


# --- _select_goal_path: number/path parsing, isolated from the real
# project's configs/goals and goals directories via a temp dir. ---


def test_select_goal_path_returns_pick_result(monkeypatch):
    # Number/out-of-range parsing itself is tui.pick's own concern now
    # (tested in test_tui.py) -- this just confirms _select_goal_path
    # offers every discovered candidate plus the custom-path escape
    # hatch, and returns tui.pick's result as-is.
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "a.yaml").write_text("dummy")
        (d / "b.yaml").write_text("dummy")
        monkeypatch.setattr(main, "_GOAL_SEARCH_DIRS", (d,))
        seen = {}

        def fake_pick(title, items, default=None, on_ctrl_c="raise"):
            seen["items"] = items
            return str(d / "b.yaml")

        monkeypatch.setattr(main.tui, "pick", fake_pick)
        assert main._select_goal_path() == str(d / "b.yaml")
        ids = [item_id for item_id, _ in seen["items"]]
        assert str(d / "a.yaml") in ids
        assert str(d / "b.yaml") in ids
        assert "__custom__" in ids


def test_select_goal_path_custom_entry_prompts_for_path(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "a.yaml").write_text("dummy")
        monkeypatch.setattr(main, "_GOAL_SEARCH_DIRS", (d,))
        monkeypatch.setattr(main.tui, "pick", lambda *a, **kw: "__custom__")
        monkeypatch.setattr(main.tui, "ask_text", lambda prompt: "/some/custom/path.yaml")
        assert main._select_goal_path() == "/some/custom/path.yaml"


def test_select_goal_path_cancel_returns_default(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "a.yaml").write_text("dummy")
        monkeypatch.setattr(main, "_GOAL_SEARCH_DIRS", (d,))
        monkeypatch.setattr(main.tui, "pick", lambda *a, **kw: None)
        assert main._select_goal_path() is None
        assert main._select_goal_path(default="configs/goals/last-used.yaml") == \
            "configs/goals/last-used.yaml"


def test_select_goal_path_no_candidates_prompts_directly(monkeypatch):
    # Zero discovered goals -- skip the picker (nothing to list) and go
    # straight to a text prompt instead.
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setattr(main, "_GOAL_SEARCH_DIRS", (Path(d),))

        def boom(*a, **kw):
            raise AssertionError("should not show a picker with zero candidates")

        monkeypatch.setattr(main.tui, "pick", boom)
        monkeypatch.setattr(main.tui, "ask_text", lambda prompt: "/typed/path.yaml")
        assert main._select_goal_path() == "/typed/path.yaml"


def test_select_goal_path_searches_both_dirs(monkeypatch):
    with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
        d1, d2 = Path(d1), Path(d2)
        (d1 / "a.yaml").write_text("dummy")
        (d2 / "b.yaml").write_text("dummy")
        monkeypatch.setattr(main, "_GOAL_SEARCH_DIRS", (d1, d2))
        seen = {}

        def fake_pick(title, items, default=None, on_ctrl_c="raise"):
            seen["items"] = items
            return str(d2 / "b.yaml")

        monkeypatch.setattr(main.tui, "pick", fake_pick)
        assert main._select_goal_path() == str(d2 / "b.yaml")
        ids = [item_id for item_id, _ in seen["items"]]
        assert str(d1 / "a.yaml") in ids
        assert str(d2 / "b.yaml") in ids


# --- _prompt_max_attempts: blank keeps the default, a positive number
# overrides it, anything else falls back to the default with a warning. ---


def test_prompt_max_attempts_blank_uses_default(monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda prompt="": "")
    assert main._prompt_max_attempts(300) == 300


def test_prompt_max_attempts_positive_number_overrides_default(monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda prompt="": "50")
    assert main._prompt_max_attempts(300) == 50


def test_prompt_max_attempts_zero_falls_back_to_default(monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda prompt="": "0")
    assert main._prompt_max_attempts(300) == 300


def test_prompt_max_attempts_non_numeric_falls_back_to_default(monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda prompt="": "abc")
    assert main._prompt_max_attempts(300) == 300


# --- _resolve_window_hint: session override vs. regions.yaml's own hint,
# and _pick_window_interactively / _recover_window_error /
# _run_with_window_recovery -- the interactive recovery flow that
# replaces a WindowNotFoundError/AmbiguousWindowError/
# ScreenCapturePermissionError crashing the whole REPL with a traceback. ---

_DUMMY_REGION_CONFIG = ocr.RegionConfig(
    page_indicator_box=(0, 0, 1, 1),
    results_title_box=(0, 0, 1, 1),
    row_templates={"single_page": [], "first_of_multi": [], "continuation": []},
    next_page_key="e",
    prev_page_key="q",
    window_title_hint="Monster Hunter Rise",
)


def _window(owner="MonsterHunterRise.exe", title="Monster Hunter Rise", wid=1):
    return capture.WindowInfo(window_id=wid, owner_name=owner, title=title, bounds=(0, 0, 1920, 1080))


def test_resolve_window_hint_falls_back_to_region_config():
    session = main._SessionState(region_config=_DUMMY_REGION_CONFIG)
    assert main._resolve_window_hint(session) == "Monster Hunter Rise"


def test_resolve_window_hint_prefers_session_override():
    session = main._SessionState(region_config=_DUMMY_REGION_CONFIG, window_hint="MonsterHunterRise.exe")
    assert main._resolve_window_hint(session) == "MonsterHunterRise.exe"


def test_pick_window_interactively_returns_matched_window(monkeypatch):
    # Index parsing/out-of-range handling is tui.pick_index's own concern
    # now (tested in test_tui.py) -- this just confirms
    # _pick_window_interactively builds the right labels and maps the
    # returned index back to the right WindowInfo.
    matches = [_window(wid=1), _window(owner="Discord.exe", title="Discord", wid=2)]
    monkeypatch.setattr(main.tui, "pick_index", lambda title, entries, default=None, on_ctrl_c="raise": 1)
    assert main._pick_window_interactively(matches) == matches[1]


def test_pick_window_interactively_cancel_returns_none(monkeypatch):
    monkeypatch.setattr(main.tui, "pick_index", lambda *a, **kw: None)
    assert main._pick_window_interactively([_window()]) is None


def test_recover_window_not_found_queries_all_windows_and_picks(monkeypatch):
    session = main._SessionState(region_config=_DUMMY_REGION_CONFIG)
    monkeypatch.setattr(capture, "find_windows", lambda hint: [_window()])
    monkeypatch.setattr(builtins, "input", lambda prompt="": "1")
    assert main._recover_window_error(capture.WindowNotFoundError("no match"), session) is True
    assert session.window_hint == "MonsterHunterRise.exe"


def test_recover_window_not_found_no_windows_at_all_gives_up(monkeypatch):
    session = main._SessionState(region_config=_DUMMY_REGION_CONFIG)
    monkeypatch.setattr(capture, "find_windows", lambda hint: [])
    assert main._recover_window_error(capture.WindowNotFoundError("no match"), session) is False
    assert session.window_hint is None


def test_recover_ambiguous_window_uses_its_own_matches_not_find_windows(monkeypatch):
    # AmbiguousWindowError already carries the matches that caused it --
    # recovery must use those directly, not re-query find_windows("").
    session = main._SessionState(region_config=_DUMMY_REGION_CONFIG)
    matches = [_window(wid=1), _window(owner="Discord.exe", title="Discord", wid=2)]

    def boom(hint):
        raise AssertionError("should not re-query find_windows")

    monkeypatch.setattr(capture, "find_windows", boom)
    monkeypatch.setattr(builtins, "input", lambda prompt="": "2")
    assert main._recover_window_error(capture.AmbiguousWindowError(matches), session) is True
    assert session.window_hint == "Discord.exe"


def test_recover_screen_capture_permission_error_gives_up_without_prompting(monkeypatch):
    # An OS permission grant -- nothing to pick, so this must not prompt.
    session = main._SessionState(region_config=_DUMMY_REGION_CONFIG)

    def boom(prompt=""):
        raise AssertionError("should not prompt for a permission error")

    monkeypatch.setattr(builtins, "input", boom)
    assert main._recover_window_error(capture.ScreenCapturePermissionError("denied"), session) is False


def test_run_with_window_recovery_retries_after_pick(monkeypatch):
    session = main._SessionState(region_config=_DUMMY_REGION_CONFIG)
    monkeypatch.setattr(capture, "find_windows", lambda hint: [_window()])
    monkeypatch.setattr(builtins, "input", lambda prompt="": "1")

    calls = []

    def action():
        calls.append(1)
        if len(calls) == 1:
            raise capture.WindowNotFoundError("no match")

    main._run_with_window_recovery(action, session)
    assert len(calls) == 2  # first call raised, second (post-pick) succeeded


def test_run_with_window_recovery_gives_up_on_cancel(monkeypatch):
    session = main._SessionState(region_config=_DUMMY_REGION_CONFIG)
    monkeypatch.setattr(capture, "find_windows", lambda hint: [_window()])
    monkeypatch.setattr(builtins, "input", lambda prompt="": "")  # cancels the pick

    calls = []

    def action():
        calls.append(1)
        raise capture.WindowNotFoundError("no match")

    main._run_with_window_recovery(action, session)
    assert len(calls) == 1  # never retried after the cancel


# --- calibrate.main(title_hint=...): explicit param wins over sys.argv --
# needed so the REPL can drive it with the session's window override
# instead of faking sys.argv. ---


def test_calibrate_main_uses_explicit_title_hint_over_argv(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["qurio-aug-calibrate", "argv-hint"])
    monkeypatch.setattr(calibrate, "configure_tesseract", lambda: None)
    seen = {}

    def fake_find_windows(hint):
        seen["hint"] = hint
        return []

    monkeypatch.setattr(capture, "find_windows", fake_find_windows)
    try:
        calibrate.main(title_hint="explicit-hint")
    except capture.WindowNotFoundError:
        pass  # no window found -- expected here, and exactly what lets
        # main.py's _run_with_window_recovery catch and offer a pick
        # instead of calibrate.main() silently killing the whole REPL
        # process via its own sys.exit() (a real bug this test caught).
    assert seen["hint"] == "explicit-hint"


def test_calibrate_main_raises_on_ambiguous_match(monkeypatch):
    # Previously just used the first match with a printed warning --
    # switched to the same shared capture.find_game_window validation
    # state_machine.py uses (raises AmbiguousWindowError) so the REPL's
    # _run_with_window_recovery can offer a pick instead of silently
    # guessing which window was meant.
    monkeypatch.setattr(sys, "argv", ["qurio-aug-calibrate"])
    monkeypatch.setattr(calibrate, "configure_tesseract", lambda: None)
    matches = [_window(wid=1), _window(owner="Discord.exe", title="Discord", wid=2)]
    monkeypatch.setattr(capture, "find_windows", lambda hint: matches)
    try:
        calibrate.main(title_hint="explicit-hint")
        assert False, "expected AmbiguousWindowError"
    except capture.AmbiguousWindowError as e:
        assert e.matches == matches


# --- _make_command_reader: falls back to plain input() when prompt_toolkit
# can't get a real console (confirmed live: its Windows output backend
# needs a genuine attached console handle and raises constructing
# PromptSession itself whenever stdout is redirected/piped, even from a
# real cmd.exe/PowerShell session -- this must degrade, not crash). ---


def test_command_reader_falls_back_to_input_when_prompt_toolkit_fails(monkeypatch, tmp_path):
    def boom(**kw):
        raise Exception("simulated: no console")

    monkeypatch.setattr(main, "PromptSession", boom)
    monkeypatch.setattr(builtins, "input", lambda prompt="": "typed-command")
    read = main._make_command_reader(main._build_completer(), tmp_path / "history")
    assert read("qurio-aug> ") == "typed-command"


# --- _classify_status_line / _status_fragments / _MENU_ITEMS: the
# arrow-key menu's colored status panel and command list. ---


def test_classify_status_line_tesseract_failed_is_bad():
    assert main._classify_status_line("tesseract: FAILED -- not installed") == "bad"


def test_classify_status_line_tesseract_ok_is_good():
    assert main._classify_status_line("tesseract: OK (version 5.4.0)") == "good"


def test_classify_status_line_tesserocr_inactive_is_warn():
    assert main._classify_status_line("tesserocr accelerator: inactive (not installed)") == "warn"


def test_classify_status_line_window_not_found_is_warn_not_bad():
    # Common/expected (e.g. the game just isn't open yet), not an error
    # -- must not read as alarming as a real tesseract failure.
    assert main._classify_status_line("window: not found (looking for 'X')") == "warn"


def test_classify_status_line_window_found_is_good():
    assert main._classify_status_line("window: found (owner='X' title='Y')") == "good"


def test_classify_status_line_ambiguous_window_is_warn():
    assert main._classify_status_line("window: 3 windows match 'X' -- ambiguous") == "warn"


def test_classify_status_line_goal_is_neutral():
    assert main._classify_status_line("goal: none selected yet") == "neutral"
    assert main._classify_status_line("goal: configs/goals/example.yaml") == "neutral"


def test_status_fragments_match_status_summary_lines():
    session = main._SessionState(region_config=_DUMMY_REGION_CONFIG)
    fragments = main._status_fragments(session)
    summary = main._status_summary(session)
    assert len(fragments) == len(summary)
    for (style_class, text), line in zip(fragments, summary):
        assert text == line + "\n"
        assert style_class.startswith("class:status-")


def test_menu_items_ids_are_all_dispatchable_via_commands_table():
    # Every arrow-menu item's id must be a name _interactive_menu's
    # dispatch (keyed off the same strings as _COMMANDS) already
    # recognizes -- guards against the two lists drifting apart.
    all_command_names = {name for names, _ in main._COMMANDS for name in names}
    for cmd_id, _ in main._MENU_ITEMS:
        assert cmd_id in all_command_names, f"{cmd_id!r} not found in _COMMANDS"


# --- _show_arrow_menu: falls back (available=False) instead of crashing
# when prompt_toolkit can't get a real console -- same failure mode
# confirmed live for _make_command_reader's PromptSession. ---


def test_show_arrow_menu_falls_back_when_construction_fails(monkeypatch):
    def boom(**kw):
        raise Exception("simulated: no console")

    monkeypatch.setattr(main.tui, "Application", boom)
    session = main._SessionState(region_config=_DUMMY_REGION_CONFIG)
    available, selected = main._show_arrow_menu(session)
    assert available is False
    assert selected is None


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
