"""Offline tests for goal_wizard.py's editing functions --
_edit_protected_skills, _edit_profile, and run_editor -- using
monkeypatched input() so no real terminal interaction is needed. Skill
names used throughout are real, already-canonical names (confirmed
against data/skills.json, e.g. via configs/goals/hellfire_strife.yaml)
so _resolve_skill_name's fast exact-match path is exercised, not its
fuzzy-correction prompt.
"""
import builtins
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qurio_aug import goal_wizard
from qurio_aug.decision import Goal, Profile, RequiredSkill
from qurio_aug.goal_config import load_goal


def _scripted_input(monkeypatch, answers):
    it = iter(answers)
    monkeypatch.setattr(builtins, "input", lambda prompt="": next(it))


def test_edit_protected_skills_add_then_remove(monkeypatch):
    _scripted_input(monkeypatch, [
        "1", "Agitator",  # add
        "1", "Burst",     # add
        "2", "1",         # remove #1 (sorted: Agitator, Burst -> removes Agitator)
        "0",              # done
    ])
    result = goal_wizard._edit_protected_skills(frozenset())
    assert result == frozenset({"Burst"})


def test_edit_profile_add_change_and_rename(monkeypatch):
    profile = Profile(required_skills=(RequiredSkill("Artillery", min_level=1),))
    _scripted_input(monkeypatch, [
        "4", "Coalescence",  # add allowed additional
        "4", "Handicraft",   # add allowed additional
        "5", "1",            # remove allowed #1 (sorted: Coalescence, Handicraft -> removes Coalescence)
        "6", "2",             # min additional skills required
        "7", "my-label",      # rename profile label
        "0",                   # done
    ])
    result = goal_wizard._edit_profile(profile)
    assert result.required_skills == (RequiredSkill("Artillery", min_level=1),)
    assert result.allowed_additional_skills == frozenset({"Handicraft"})
    assert result.min_additional_skills == 2
    assert result.name == "my-label"


def test_edit_profile_change_required_skill_level(monkeypatch):
    profile = Profile(required_skills=(RequiredSkill("Artillery", min_level=1),))
    _scripted_input(monkeypatch, [
        "3", "1", "3",  # change required skill #1's minimum level to 3
        "0",             # done
    ])
    result = goal_wizard._edit_profile(profile)
    assert result.required_skills == (RequiredSkill("Artillery", min_level=3),)


def test_edit_profile_refuses_to_finish_with_no_required_skills(monkeypatch):
    profile = Profile(required_skills=(RequiredSkill("Artillery", min_level=1),))
    _scripted_input(monkeypatch, [
        "2", "1",             # remove the only required skill
        "0",                   # try to finish -- must be refused (no required skills left)
        "1", "Burst", "1",     # so add one back, with minimum level 1
        "0",                    # now finishing is allowed
    ])
    result = goal_wizard._edit_profile(profile)
    assert result.required_skills == (RequiredSkill("Burst", min_level=1),)


def test_run_editor_end_to_end_saves_correctly(monkeypatch):
    goal = Goal(
        name="test-goal",
        augment_type="skills_plus",
        profiles=(Profile(required_skills=(RequiredSkill("Artillery", min_level=1),)),),
        protected_skills=frozenset(),
    )
    _scripted_input(monkeypatch, [
        "1", "1", "Burst", "0",   # edit protected skills -> add Burst, done
        "2", "1",                  # edit profile 1
        "3", "1", "3",              # change required skill #1's min level to 3
        "0",                         # done editing this profile
        "0",                          # save and exit
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
