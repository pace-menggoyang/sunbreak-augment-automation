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

from qurio_aug import main, state_machine
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


# --- _select_goal_path: number/path parsing, isolated from the real
# project's configs/goals and goals directories via a temp dir. ---


def test_select_goal_path_by_number(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "a.yaml").write_text("dummy")
        (d / "b.yaml").write_text("dummy")
        monkeypatch.setattr(main, "_GOAL_SEARCH_DIRS", (d,))
        monkeypatch.setattr(builtins, "input", lambda prompt="": "2")
        assert main._select_goal_path() == str(d / "b.yaml")


def test_select_goal_path_custom_typed_path(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setattr(main, "_GOAL_SEARCH_DIRS", (Path(d),))
        monkeypatch.setattr(builtins, "input", lambda prompt="": "/some/custom/path.yaml")
        assert main._select_goal_path() == "/some/custom/path.yaml"


def test_select_goal_path_blank_cancels(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setattr(main, "_GOAL_SEARCH_DIRS", (Path(d),))
        monkeypatch.setattr(builtins, "input", lambda prompt="": "")
        assert main._select_goal_path() is None


def test_select_goal_path_out_of_range_number_returns_none(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "a.yaml").write_text("dummy")
        monkeypatch.setattr(main, "_GOAL_SEARCH_DIRS", (d,))
        monkeypatch.setattr(builtins, "input", lambda prompt="": "99")
        assert main._select_goal_path() is None


def test_select_goal_path_searches_both_dirs(monkeypatch):
    with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
        d1, d2 = Path(d1), Path(d2)
        (d1 / "a.yaml").write_text("dummy")
        (d2 / "b.yaml").write_text("dummy")
        monkeypatch.setattr(main, "_GOAL_SEARCH_DIRS", (d1, d2))
        monkeypatch.setattr(builtins, "input", lambda prompt="": "2")
        assert main._select_goal_path() == str(d2 / "b.yaml")


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
