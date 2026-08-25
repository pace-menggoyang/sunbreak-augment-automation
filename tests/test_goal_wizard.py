"""Offline tests for goal_wizard.py's editing functions --
_edit_protected_skills, _edit_profile, and run_editor -- using
monkeypatched tui functions (pick/pick_index/ask_text/ask_int/
ask_text_with_completion) so no real terminal interaction is needed.
Skill names used throughout are real, already-canonical names (confirmed
against data/skills.json, e.g. via configs/goals/hellfire_strife.yaml)
so _resolve_skill_name's fast exact-match path is exercised, not its
fuzzy-correction prompt.

_scripted_calls verifies not just the *values* each tui call returns but
which tui function is called at each step, in order -- since a wrong
call (e.g. goal_wizard calling tui.pick when this expected tui.ask_text)
is exactly the kind of bug a plain value-only mock would hide.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qurio_aug import goal_wizard
from qurio_aug.decision import Goal, Profile, RequiredSkill
from qurio_aug.goal_config import load_goal

_SCRIPTABLE_TUI_FUNCTIONS = ("pick", "pick_index", "ask_yes_no", "ask_text", "ask_text_with_completion", "ask_int")


def _scripted_calls(monkeypatch, calls):
    """calls: list of (tui_function_name, return_value) in the exact
    order goal_wizard is expected to call them.
    """
    it = iter(calls)

    def make(name):
        def fn(*args, **kwargs):
            expected_name, value = next(it)
            assert expected_name == name, f"expected next call to be tui.{expected_name}, got tui.{name}"
            return value
        return fn

    for name in _SCRIPTABLE_TUI_FUNCTIONS:
        monkeypatch.setattr(goal_wizard.tui, name, make(name))


def test_edit_protected_skills_add_then_remove(monkeypatch):
    _scripted_calls(monkeypatch, [
        ("pick", "add"), ("ask_text_with_completion", "Agitator"),
        ("pick", "add"), ("ask_text_with_completion", "Burst"),
        ("pick", "remove"), ("pick_index", 0),  # sorted: Agitator, Burst -> removes Agitator
        ("pick", "done"),
    ])
    result = goal_wizard._edit_protected_skills(frozenset())
    assert result == frozenset({"Burst"})


def test_edit_profile_add_change_and_rename(monkeypatch):
    profile = Profile(required_skills=(RequiredSkill("Artillery", min_level=1),))
    _scripted_calls(monkeypatch, [
        ("pick", "add_allowed"), ("ask_text_with_completion", "Coalescence"),
        ("pick", "add_allowed"), ("ask_text_with_completion", "Handicraft"),
        ("pick", "remove_allowed"), ("pick_index", 0),  # sorted: Coalescence, Handicraft -> removes Coalescence
        ("pick", "change_min_additional"), ("ask_int", 2),
        ("pick", "rename"), ("ask_text", "my-label"),
        ("pick", "done"),
    ])
    result = goal_wizard._edit_profile(profile)
    assert result.required_skills == (RequiredSkill("Artillery", min_level=1),)
    assert result.allowed_additional_skills == frozenset({"Handicraft"})
    assert result.min_additional_skills == 2
    assert result.name == "my-label"


def test_edit_profile_change_required_skill_level(monkeypatch):
    profile = Profile(required_skills=(RequiredSkill("Artillery", min_level=1),))
    _scripted_calls(monkeypatch, [
        ("pick", "change_level"), ("pick_index", 0), ("ask_int", 3),
        ("pick", "done"),
    ])
    result = goal_wizard._edit_profile(profile)
    assert result.required_skills == (RequiredSkill("Artillery", min_level=3),)


def test_edit_profile_refuses_to_finish_with_no_required_skills(monkeypatch):
    profile = Profile(required_skills=(RequiredSkill("Artillery", min_level=1),))
    _scripted_calls(monkeypatch, [
        ("pick", "remove_required"), ("pick_index", 0),  # remove the only required skill
        ("pick", "done"),  # refused -- no required skills left, loops back
        ("pick", "add_required"), ("ask_text_with_completion", "Burst"), ("ask_int", 1),
        ("pick", "done"),  # now allowed
    ])
    result = goal_wizard._edit_profile(profile)
    assert result.required_skills == (RequiredSkill("Burst", min_level=1),)


def test_run_wizard_end_to_end_writes_yaml(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setattr(goal_wizard, "GOALS_DIR", Path(d))
        _scripted_calls(monkeypatch, [
            ("ask_text", "Test Goal"),                  # name
            ("pick", "skills_plus"),                     # augment type
            ("ask_text_with_completion", ""),             # protected skills -- none
            ("ask_text", ""),                              # profile 1 label -- blank
            ("ask_text_with_completion", "Artillery"),      # required skill 1
            ("ask_text_with_completion", ""),                # required skills done
            ("ask_int", 1),                                   # min level for Artillery
            ("ask_text_with_completion", ""),                  # allowed additional -- none
            ("ask_yes_no", False),                              # add another profile? no
        ])
        out_path = goal_wizard.run_wizard()
        goal = load_goal(out_path)
        assert goal.name == "Test Goal"
        assert goal.augment_type == "skills_plus"
        assert goal.profiles[0].required_skills == (RequiredSkill("Artillery", min_level=1),)


def test_run_editor_end_to_end_saves_correctly(monkeypatch):
    goal = Goal(
        name="test-goal",
        augment_type="skills_plus",
        profiles=(Profile(required_skills=(RequiredSkill("Artillery", min_level=1),)),),
        protected_skills=frozenset(),
    )
    _scripted_calls(monkeypatch, [
        ("pick", "edit_protected"),
        ("pick", "add"), ("ask_text_with_completion", "Burst"),
        ("pick", "done"),
        ("pick", "edit_profile"), ("pick_index", 0),  # only one profile
        ("pick", "change_level"), ("pick_index", 0), ("ask_int", 3),
        ("pick", "done"),  # done editing this profile
        ("pick", "save"),  # save and exit
    ])
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "test-goal.yaml"
        goal_wizard.run_editor(goal, path)
        reloaded = load_goal(path)
        assert reloaded.protected_skills == frozenset({"Burst"})
        assert reloaded.profiles[0].required_skills == (RequiredSkill("Artillery", min_level=3),)


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
