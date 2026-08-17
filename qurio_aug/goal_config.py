"""Load a Goal (target skill config) from a YAML file, e.g.
configs/goals/example.yaml.

Schema:
  name: str
  augment_type: skills_plus | regular
  protected_skills: [str, ...]        # optional, global across all profiles
  profiles:
    - name: str                       # optional label, shows up in decision reasons
      required_skills:
        - name: str
          min_level: int               # optional, default 1
      allowed_additional_skills: [str, ...]  # optional
      min_additional_skills: int       # optional, default 0

A roll is accepted if it matches ANY profile (OR semantics). See
decision.py for full evaluation rules.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from qurio_aug import skills_db
from qurio_aug.decision import Goal, Profile, RequiredSkill


class GoalValidationError(ValueError):
    """Raised by validate_goal for a skill name referenced anywhere in a
    goal config that isn't in the canonical skill list. Without this, a
    typo'd skill name loads silently: skills_db.match_skill_name already
    treats unmatched OCR text as "no match" (exactly what a real read of
    every row this typo would ever apply to would also produce), so the
    config runs, farms forever, and never once accepts a result, with
    nothing to explain why. Catching it at load time instead turns a
    silent forever-reject into an immediate, actionable error.
    """


def _check_skill_name(name: str, context: str, errors: list[str]) -> None:
    if name in skills_db.all_skill_names():
        return
    skill, _ = skills_db.match_skill_name(name)
    if skill is not None:
        errors.append(f"{context}: {name!r} is not a recognized skill -- did you mean {skill.name!r}?")
    else:
        errors.append(f"{context}: {name!r} is not a recognized skill and no close match was found")


def validate_goal(goal: Goal) -> None:
    errors: list[str] = []
    for name in goal.protected_skills:
        _check_skill_name(name, "protected_skills", errors)
    for profile in goal.profiles:
        label = profile.name or "profile"
        for req in profile.required_skills:
            _check_skill_name(req.name, f"{label}.required_skills", errors)
        for name in profile.allowed_additional_skills:
            _check_skill_name(name, f"{label}.allowed_additional_skills", errors)
    if errors:
        raise GoalValidationError("\n".join(errors))


def _load_profile(raw: dict) -> Profile:
    required = [
        RequiredSkill(name=r["name"], min_level=r.get("min_level", 1))
        for r in raw["required_skills"]
    ]
    return Profile(
        required_skills=tuple(required),
        allowed_additional_skills=frozenset(raw.get("allowed_additional_skills", [])),
        min_additional_skills=raw.get("min_additional_skills", 0),
        name=raw.get("name", ""),
    )


def load_goal(path: Path | str) -> Goal:
    path = Path(path)
    raw = yaml.safe_load(path.read_text())

    goal = Goal(
        name=raw.get("name", path.stem),
        augment_type=raw["augment_type"],
        profiles=tuple(_load_profile(p) for p in raw["profiles"]),
        protected_skills=frozenset(raw.get("protected_skills", [])),
    )
    validate_goal(goal)
    return goal
