"""Interactive CLI wizard that asks a series of questions and writes a
goal YAML matching goal_config.py's schema -- for people who'd rather
answer prompts than hand-write YAML from scratch. Every skill name typed
anywhere in the flow goes through the same fuzzy-match helper already
used to correct OCR noise (skills_db.match_skill_name), so a typo gets
caught and offered a correction live, instead of only surfacing later via
goal_config.validate_goal (which still runs too, as a final check, since
a wizard bug shouldn't be trusted any more than a hand-edited file's typo
would be).

Run via `python -m qurio_aug.main --wizard` (see main.py).
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from qurio_aug import skills_db
from qurio_aug.goal_config import validate_goal
from qurio_aug.decision import Goal, Profile, RequiredSkill

GOALS_DIR = Path("goals")


def _ask(prompt: str) -> str:
    return input(prompt).strip()


def _ask_yes_no(prompt: str, default: bool = False) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    raw = _ask(f"{prompt} {suffix} ").lower()
    if not raw:
        return default
    return raw.startswith("y")


def _ask_int(prompt: str, default: int) -> int:
    raw = _ask(f"{prompt} (default {default}): ")
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"  not a number, using default ({default})")
        return default


def _ask_skill_list(prompt_label: str) -> list[str]:
    print(f"{prompt_label} (enter one at a time, blank line when done):")
    names = []
    while True:
        raw = _ask(f"  skill {len(names) + 1} (or blank to finish): ")
        if not raw:
            break
        names.append(_resolve_skill_name(raw))
    return names


def _resolve_skill_name(raw: str) -> str:
    """Given an already-typed skill name, live-correct it via the same
    fuzzy match used to clean up OCR noise -- catches a typo as it's
    entered instead of leaving it to fail silently at runtime (a name
    goal_config.validate_goal would also reject at load time, but here we
    can just ask what was actually meant).
    """
    names = set(skills_db.all_skill_names())
    while True:
        if raw in names:
            return raw
        skill, score = skills_db.match_skill_name(raw)
        if skill is not None and skill.name != raw:
            if _ask_yes_no(f"    '{raw}' isn't an exact match -- did you mean '{skill.name}'?", default=True):
                return skill.name
            raw = _ask("    type it again: ").strip()
            continue
        if skill is not None:
            return skill.name
        if _ask_yes_no(f"    '{raw}' isn't a recognized skill and no close match was found -- use it anyway?"):
            return raw
        raw = _ask("    type it again: ").strip()


def _ask_profile(index: int) -> Profile:
    print(f"\n--- Profile {index} ---")
    label = _ask("Optional label for this profile (blank to skip): ")

    required_names = []
    print("Required skills for this profile (at least one, blank line when done):")
    while True:
        raw = _ask(f"  required skill {len(required_names) + 1} (or blank to finish): ")
        if not raw:
            if required_names:
                break
            print("  a profile needs at least one required skill.")
            continue
        required_names.append(_resolve_skill_name(raw))

    required = []
    for name in required_names:
        min_level = _ask_int(f"  minimum level for '{name}'", default=1)
        required.append(RequiredSkill(name=name, min_level=min_level))

    allowed = _ask_skill_list("Additional skills that are OK to gain alongside the required ones (optional)")
    min_additional = 0
    if allowed:
        min_additional = _ask_int("Minimum number of those additional skills required", default=0)

    return Profile(
        required_skills=tuple(required),
        allowed_additional_skills=frozenset(allowed),
        min_additional_skills=min_additional,
        name=label,
    )


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "goal"


def run_wizard() -> Path:
    print("Qurio augmentation goal wizard -- answers build a goal YAML you\n"
          "can review/edit afterward. Press Ctrl+C at any point to cancel.\n")

    name = _ask("Goal name (used in logs and the output filename): ")
    while not name:
        name = _ask("Goal name can't be blank: ")

    augment_type = ""
    while augment_type not in ("skills_plus", "regular"):
        augment_type = _ask("Augment type -- 'skills_plus' or 'regular' (must match what "
                             "you select in-game): ").strip().lower()

    protected = _ask_skill_list(
        "\nProtected skills -- reject any roll that loses or fully removes "
        "these, no matter what else it does (optional)"
    )

    profiles = []
    i = 1
    while True:
        profiles.append(_ask_profile(i))
        i += 1
        if not _ask_yes_no("\nAdd another profile (roll accepted if ANY profile matches)?"):
            break

    goal = Goal(
        name=name,
        augment_type=augment_type,
        profiles=tuple(profiles),
        protected_skills=frozenset(protected),
    )
    validate_goal(goal)  # belt-and-suspenders -- a wizard bug shouldn't be trusted any more than a typo would be

    raw = {
        "name": goal.name,
        "augment_type": goal.augment_type,
    }
    if goal.protected_skills:
        raw["protected_skills"] = sorted(goal.protected_skills)
    raw["profiles"] = []
    for profile in goal.profiles:
        p = {}
        if profile.name:
            p["name"] = profile.name
        p["required_skills"] = [
            {"name": r.name} if r.min_level == 1 else {"name": r.name, "min_level": r.min_level}
            for r in profile.required_skills
        ]
        if profile.allowed_additional_skills:
            p["allowed_additional_skills"] = sorted(profile.allowed_additional_skills)
        if profile.min_additional_skills:
            p["min_additional_skills"] = profile.min_additional_skills
        raw["profiles"].append(p)

    GOALS_DIR.mkdir(exist_ok=True)
    out_path = GOALS_DIR / f"{_slug(name)}.yaml"
    out_path.write_text(yaml.safe_dump(raw, sort_keys=False, default_flow_style=False))

    print(f"\nWrote {out_path.resolve()}")
    print(f"Next: python -m qurio_aug.main --goal {out_path} --dry-run")
    return out_path


if __name__ == "__main__":
    run_wizard()
