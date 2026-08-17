"""Offline tests for goal_config.validate_goal -- the eager check that
catches a typo'd skill name in a goal YAML at load time instead of letting
it load silently and just never match anything (see GoalValidationError's
docstring). Uses hand-built Goal/Profile fixtures plus a couple of
realistic human-typo strings (not just the OCR-noise cases already covered
in test_skills_db.py), since match_skill_name's scorer was tuned for OCR
bleed specifically, not typing mistakes.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qurio_aug.decision import Goal, Profile, RequiredSkill
from qurio_aug.goal_config import GoalValidationError, load_goal, validate_goal


def _goal(*, protected=(), required=("Artillery",), allowed=()) -> Goal:
    profile = Profile(
        required_skills=tuple(RequiredSkill(name=n) for n in required),
        allowed_additional_skills=frozenset(allowed),
    )
    return Goal(
        name="test-goal",
        augment_type="skills_plus",
        profiles=(profile,),
        protected_skills=frozenset(protected),
    )


def test_valid_goal_passes_validation():
    validate_goal(_goal(protected=["Weakness Exploit"], required=["Artillery"], allowed=["Diversion"]))


def test_typo_in_protected_skill_raises_with_suggestion():
    try:
        validate_goal(_goal(protected=["Weakness Exploitt"]))
        assert False, "expected GoalValidationError"
    except GoalValidationError as e:
        assert "Weakness Exploit" in str(e)


def test_typo_in_required_skill_raises_with_suggestion():
    try:
        validate_goal(_goal(required=["attak boost"]))
        assert False, "expected GoalValidationError"
    except GoalValidationError as e:
        assert "Attack Boost" in str(e)


def test_typo_in_allowed_additional_skill_raises_with_suggestion():
    try:
        validate_goal(_goal(allowed=["Critcal Boost"]))
        assert False, "expected GoalValidationError"
    except GoalValidationError as e:
        assert "Critical Boost" in str(e)


def test_completely_unmatched_skill_raises_without_a_bogus_suggestion():
    try:
        validate_goal(_goal(required=["Xyzzy Nonsense Skill"]))
        assert False, "expected GoalValidationError"
    except GoalValidationError as e:
        assert "no close match was found" in str(e)


def test_multiple_bad_names_are_all_reported_together():
    try:
        validate_goal(_goal(protected=["Weakness Exploitt"], required=["attak boost"]))
        assert False, "expected GoalValidationError"
    except GoalValidationError as e:
        assert "Weakness Exploit" in str(e) and "Attack Boost" in str(e)


def test_load_goal_raises_goal_validation_error_on_typo():
    yaml_text = """
name: broken-goal
augment_type: skills_plus
protected_skills:
  - Weaknes Exploit
profiles:
  - required_skills:
      - name: Artillery
"""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "broken.yaml"
        path.write_text(yaml_text)
        try:
            load_goal(path)
            assert False, "expected GoalValidationError"
        except GoalValidationError as e:
            assert "Weakness Exploit" in str(e)


def test_load_goal_example_yaml_still_loads_cleanly():
    # Regression check against the real shipped example -- validation
    # must not false-positive on legitimate configs.
    example = Path(__file__).resolve().parent.parent / "configs" / "goals" / "example.yaml"
    goal = load_goal(example)
    assert goal.profiles


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
