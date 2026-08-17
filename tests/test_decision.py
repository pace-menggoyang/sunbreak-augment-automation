"""Offline unit tests for the decision engine, using fixture rolls modeled
on the actual step-references screenshots (no live game/OCR required).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qurio_aug.decision import Goal, Profile, RequiredSkill, SkillResult, any_profile_reachable, evaluate
from qurio_aug.goal_config import load_goal

# Roll from step-4-augmentation-result.png: Artillery +1, Diversion +1,
# Critical Boost fully removed ("None").
ROLL_ARTILLERY = [
    SkillResult("Artillery", delta=1),
    SkillResult("Diversion", delta=1),
    SkillResult("Critical Boost", removed=True),
]

ROLL_NO_SKILLS: list[SkillResult] = []


def _single_profile_goal(**profile_kwargs) -> Goal:
    return Goal(
        name="test",
        augment_type="skills_plus",
        profiles=(Profile(**profile_kwargs),),
    )


def test_accepts_when_required_skill_met_and_no_protected_touched():
    goal = _single_profile_goal(
        required_skills=(RequiredSkill("Artillery", min_level=1),),
        allowed_additional_skills=frozenset({"Diversion"}),
    )
    goal = Goal(goal.name, goal.augment_type, goal.profiles, protected_skills=frozenset({"Weakness Exploit"}))
    decision = evaluate(goal, ROLL_ARTILLERY)
    assert decision.accepted, decision.reason


def test_rejects_when_protected_skill_removed():
    goal = _single_profile_goal(
        required_skills=(RequiredSkill("Artillery", min_level=1),),
        allowed_additional_skills=frozenset({"Diversion"}),
    )
    goal = Goal(goal.name, goal.augment_type, goal.profiles, protected_skills=frozenset({"Critical Boost"}))
    decision = evaluate(goal, ROLL_ARTILLERY)
    assert not decision.accepted
    assert "Critical Boost" in decision.reason
    assert "removed entirely" in decision.reason


def test_rejects_when_required_skill_missing():
    goal = _single_profile_goal(required_skills=(RequiredSkill("Weakness Exploit", min_level=1),))
    decision = evaluate(goal, ROLL_ARTILLERY)
    assert not decision.accepted
    assert "Weakness Exploit" in decision.reason


def test_rejects_when_required_skill_below_min_level():
    goal = _single_profile_goal(required_skills=(RequiredSkill("Artillery", min_level=2),))
    decision = evaluate(goal, ROLL_ARTILLERY)
    assert not decision.accepted
    assert "got +1, need +2" in decision.reason


def test_rejects_unexpected_gain_outside_allowed_pool():
    goal = _single_profile_goal(
        required_skills=(RequiredSkill("Artillery", min_level=1),),
        allowed_additional_skills=frozenset(),  # Diversion gain not allowed
    )
    decision = evaluate(goal, ROLL_ARTILLERY)
    assert not decision.accepted
    assert "Diversion" in decision.reason


def test_non_protected_skill_loss_is_fine():
    goal = _single_profile_goal(
        required_skills=(RequiredSkill("Artillery", min_level=1),),
        allowed_additional_skills=frozenset({"Diversion"}),
    )
    decision = evaluate(goal, ROLL_ARTILLERY)
    assert decision.accepted, decision.reason


def test_rejects_empty_roll():
    goal = _single_profile_goal(required_skills=(RequiredSkill("Artillery", min_level=1),))
    decision = evaluate(goal, ROLL_NO_SKILLS)
    assert not decision.accepted


# --- Multi-profile goal (Mail of Hellfire + Strife), loaded from the real
# configs/goals/hellfire_strife.yaml so these tests track the actual config
# rather than a hand-duplicated copy. Two anchor skills (Mail of Hellfire,
# Strife), each independently accepted at level 2 alone, OR at level 1 plus
# one companion (the other anchor, or anything in the bonus pool). See the
# real examples this was built from in the conversation this config was
# corrected from -- an earlier "both anchors always required" version
# wrongly rejected a roll that should have passed.

HELLFIRE_STRIFE_GOAL = load_goal(
    Path(__file__).resolve().parent.parent / "configs" / "goals" / "hellfire_strife.yaml"
)


def test_hellfire_strife_anchor_alone_at_level_2_matches():
    roll = [SkillResult("Mail of Hellfire", delta=2), SkillResult("Partbreaker", delta=-1)]
    decision = evaluate(HELLFIRE_STRIFE_GOAL, roll)
    assert decision.accepted, decision.reason
    assert "hellfire-double-level" in decision.reason


def test_hellfire_strife_other_anchor_alone_at_level_2_matches():
    roll = [SkillResult("Strife", delta=2), SkillResult("Critical Boost", delta=-1)]
    decision = evaluate(HELLFIRE_STRIFE_GOAL, roll)
    assert decision.accepted, decision.reason
    assert "strife-double-level" in decision.reason


def test_hellfire_strife_level_2_anchor_allows_bonus_alongside():
    # Confirmed: a bonus skill showing up alongside an already-satisfied
    # level-2 anchor still passes, even though it's unlikely given budget
    # constraints.
    roll = [SkillResult("Mail of Hellfire", delta=2), SkillResult("Element Exploit", delta=1)]
    decision = evaluate(HELLFIRE_STRIFE_GOAL, roll)
    assert decision.accepted, decision.reason


def test_hellfire_strife_both_anchors_at_level_1_matches():
    roll = [
        SkillResult("Mail of Hellfire", delta=1),
        SkillResult("Strife", delta=1),
        SkillResult("Partbreaker", delta=-2),
    ]
    decision = evaluate(HELLFIRE_STRIFE_GOAL, roll)
    assert decision.accepted, decision.reason


def test_hellfire_strife_anchor_plus_pool_companion_matches():
    roll = [
        SkillResult("Mail of Hellfire", delta=1),
        SkillResult("Element Exploit", delta=1),
        SkillResult("Partbreaker", delta=-1),
    ]
    decision = evaluate(HELLFIRE_STRIFE_GOAL, roll)
    assert decision.accepted, decision.reason
    assert "hellfire-plus-companion" in decision.reason


def test_hellfire_strife_anchor_alone_at_level_1_rejected():
    roll = [SkillResult("Mail of Hellfire", delta=1)]
    decision = evaluate(HELLFIRE_STRIFE_GOAL, roll)
    assert not decision.accepted


def test_hellfire_strife_rejects_companion_outside_pool():
    roll = [SkillResult("Mail of Hellfire", delta=1), SkillResult("Agitator", delta=1)]
    decision = evaluate(HELLFIRE_STRIFE_GOAL, roll)
    assert not decision.accepted


def test_hellfire_strife_neither_anchor_present_rejected():
    roll = [SkillResult("Element Exploit", delta=1), SkillResult("Wind Mantle", delta=1)]
    decision = evaluate(HELLFIRE_STRIFE_GOAL, roll)
    assert not decision.accepted


def test_hellfire_strife_rejects_blood_awakening_removed_even_if_profile_matches():
    roll = [
        SkillResult("Mail of Hellfire", delta=2),
        SkillResult("Strife", delta=2),
        SkillResult("Blood Awakening", removed=True),
    ]
    decision = evaluate(HELLFIRE_STRIFE_GOAL, roll)
    assert not decision.accepted
    assert "Blood Awakening" in decision.reason


def test_hellfire_strife_rejects_blood_awakening_partial_loss():
    roll = [
        SkillResult("Mail of Hellfire", delta=1),
        SkillResult("Strife", delta=1),
        SkillResult("Maximum Might", delta=1),
        SkillResult("Blood Awakening", delta=-1),
    ]
    decision = evaluate(HELLFIRE_STRIFE_GOAL, roll)
    assert not decision.accepted
    assert "Blood Awakening" in decision.reason


def test_hellfire_strife_rejects_blood_awakening_removed_even_with_companion_match():
    # Otherwise a perfect hellfire-plus-companion match -- protection still wins.
    roll = [
        SkillResult("Mail of Hellfire", delta=1),
        SkillResult("Strife", delta=1),
        SkillResult("Wind Mantle", delta=1),
        SkillResult("Blood Awakening", removed=True),
    ]
    decision = evaluate(HELLFIRE_STRIFE_GOAL, roll)
    assert not decision.accepted
    assert "Blood Awakening" in decision.reason


# --- any_profile_reachable: the pre-check used by state_machine.py to
# decide whether page 2+ is worth reading at all before any gain has been
# confirmed to matter. ---


def test_any_profile_reachable_true_when_a_required_skill_is_met():
    gains = [SkillResult("Mail of Hellfire", delta=1)]
    assert any_profile_reachable(HELLFIRE_STRIFE_GOAL.profiles, gains)


def test_any_profile_reachable_false_when_no_gains_match_any_profile():
    gains = [SkillResult("Agitator", delta=1)]  # not an anchor for any profile
    assert not any_profile_reachable(HELLFIRE_STRIFE_GOAL.profiles, gains)


def test_any_profile_reachable_false_when_no_gains_at_all():
    assert not any_profile_reachable(HELLFIRE_STRIFE_GOAL.profiles, [])


def test_any_profile_reachable_true_even_if_level_still_short_of_double_level():
    # Mail of Hellfire +1 doesn't satisfy hellfire-double-level (needs +2)
    # but does satisfy hellfire-plus-companion's required_skills (+1) --
    # reachable is about "some profile's required_skills could still be
    # met", not "already accepted".
    gains = [SkillResult("Mail of Hellfire", delta=1)]
    assert any_profile_reachable(HELLFIRE_STRIFE_GOAL.profiles, gains)


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
