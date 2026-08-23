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


def _goal_to_raw_dict(goal: Goal) -> dict:
    """The inverse of goal_config._load_profile/load_goal -- shared by
    run_wizard and run_editor so both write the same shape (and any future
    schema tweak only needs updating here, not twice).
    """
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
    return raw


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

    raw = _goal_to_raw_dict(goal)

    GOALS_DIR.mkdir(exist_ok=True)
    out_path = GOALS_DIR / f"{_slug(name)}.yaml"
    out_path.write_text(yaml.safe_dump(raw, sort_keys=False, default_flow_style=False))

    print(f"\nWrote {out_path.resolve()}")
    print(f"Next: python -m qurio_aug.main --goal {out_path} --dry-run")
    return out_path


def _display_goal(goal: Goal) -> None:
    print(f"\nname: {goal.name}")
    print(f"augment_type: {goal.augment_type}")
    print("protected_skills: " + (", ".join(sorted(goal.protected_skills)) or "(none)"))
    for i, p in enumerate(goal.profiles, 1):
        label = f" ({p.name})" if p.name else ""
        print(f"profile {i}{label}:")
        for r in p.required_skills:
            print(f"  required: {r.name} (min level {r.min_level})")
        if p.allowed_additional_skills:
            print(f"  allowed additional: {', '.join(sorted(p.allowed_additional_skills))} "
                  f"(min {p.min_additional_skills} required)")


def _edit_protected_skills(protected: frozenset[str]) -> frozenset[str]:
    current = set(protected)
    while True:
        print("\nProtected skills: " + (", ".join(sorted(current)) or "(none)"))
        print("  1. Add a skill\n  2. Remove a skill\n  0. Done")
        choice = _ask("choice: ")
        if choice == "1":
            name = _ask("  skill to protect: ")
            if name:
                current.add(_resolve_skill_name(name))
        elif choice == "2":
            if not current:
                print("  nothing to remove.")
                continue
            names = sorted(current)
            for i, n in enumerate(names, 1):
                print(f"    {i}. {n}")
            raw = _ask("  remove which number (blank to cancel): ")
            if raw.isdigit() and 1 <= int(raw) <= len(names):
                current.discard(names[int(raw) - 1])
        elif choice in ("0", ""):
            return frozenset(current)
        else:
            print(f"  {choice!r} isn't one of the options above.")


def _edit_profile(profile: Profile) -> Profile:
    required = list(profile.required_skills)
    allowed = set(profile.allowed_additional_skills)
    min_additional = profile.min_additional_skills
    label = profile.name

    while True:
        print(f"\nEditing profile{f' ({label})' if label else ''}:")
        for i, r in enumerate(required, 1):
            print(f"  {i}. {r.name} (min level {r.min_level})")
        print("  allowed additional: " + (", ".join(sorted(allowed)) or "(none)")
              + (f" (min {min_additional} required)" if allowed else ""))
        print("""
  1. Add a required skill
  2. Remove a required skill
  3. Change a required skill's minimum level
  4. Add an allowed additional skill
  5. Remove an allowed additional skill
  6. Change minimum additional skills required
  7. Rename this profile's label
  0. Done editing this profile""")
        choice = _ask("choice: ")
        if choice == "1":
            name = _ask("  skill name: ")
            if name:
                name = _resolve_skill_name(name)
                level = _ask_int(f"  minimum level for '{name}'", default=1)
                required.append(RequiredSkill(name=name, min_level=level))
        elif choice == "2":
            if not required:
                print("  nothing to remove.")
                continue
            for i, r in enumerate(required, 1):
                print(f"    {i}. {r.name}")
            raw = _ask("  remove which number (blank to cancel): ")
            if raw.isdigit() and 1 <= int(raw) <= len(required):
                del required[int(raw) - 1]
        elif choice == "3":
            if not required:
                print("  nothing to change.")
                continue
            for i, r in enumerate(required, 1):
                print(f"    {i}. {r.name} (currently min level {r.min_level})")
            raw = _ask("  change which number (blank to cancel): ")
            if raw.isdigit() and 1 <= int(raw) <= len(required):
                idx = int(raw) - 1
                new_level = _ask_int(f"  new minimum level for '{required[idx].name}'",
                                      default=required[idx].min_level)
                required[idx] = RequiredSkill(name=required[idx].name, min_level=new_level)
        elif choice == "4":
            name = _ask("  skill name: ")
            if name:
                allowed.add(_resolve_skill_name(name))
        elif choice == "5":
            if not allowed:
                print("  nothing to remove.")
                continue
            names = sorted(allowed)
            for i, n in enumerate(names, 1):
                print(f"    {i}. {n}")
            raw = _ask("  remove which number (blank to cancel): ")
            if raw.isdigit() and 1 <= int(raw) <= len(names):
                allowed.discard(names[int(raw) - 1])
        elif choice == "6":
            min_additional = _ask_int("  minimum additional skills required", default=min_additional)
        elif choice == "7":
            label = _ask("  new label (blank to clear): ")
        elif choice in ("0", ""):
            if not required:
                print("  a profile needs at least one required skill -- add one before finishing.")
                continue
            return Profile(
                required_skills=tuple(required),
                allowed_additional_skills=frozenset(allowed),
                min_additional_skills=min_additional,
                name=label,
            )
        else:
            print(f"  {choice!r} isn't one of the options above.")


def run_editor(goal: Goal, path: Path) -> None:
    """Interactive editor for an already-loaded goal, saving back to `path`
    on exit -- for tweaking a level requirement or adding a skill to an
    allowed pool without hand-editing YAML. Reuses run_wizard's building
    blocks (skill-name resolution, int/yes-no prompts) rather than building
    a Goal from scratch. Ctrl+C at any point discards changes, same
    convention as run_wizard (propagates out to main()'s top-level catch).
    """
    name = goal.name
    augment_type = goal.augment_type
    protected = goal.protected_skills
    profiles = list(goal.profiles)

    print(f"\nEditing {path} -- Ctrl+C at any point discards changes.")
    while True:
        _display_goal(Goal(name=name, augment_type=augment_type,
                            profiles=tuple(profiles), protected_skills=protected))
        print("""
1. Edit protected skills
2. Edit an existing profile
3. Add a new profile
4. Remove a profile
5. Rename goal / change augment type
0. Save and exit""")
        choice = _ask("\nChoice: ")
        if choice == "1":
            protected = _edit_protected_skills(protected)
        elif choice == "2":
            if not profiles:
                print("  no profiles to edit.")
                continue
            for i, p in enumerate(profiles, 1):
                print(f"    {i}. {p.name or '(unlabeled)'}")
            raw = _ask("  edit which number (blank to cancel): ")
            if raw.isdigit() and 1 <= int(raw) <= len(profiles):
                profiles[int(raw) - 1] = _edit_profile(profiles[int(raw) - 1])
        elif choice == "3":
            profiles.append(_ask_profile(len(profiles) + 1))
        elif choice == "4":
            if not profiles:
                print("  no profiles to remove.")
                continue
            if len(profiles) == 1:
                print("  a goal needs at least one profile -- add another before removing this one.")
                continue
            for i, p in enumerate(profiles, 1):
                print(f"    {i}. {p.name or '(unlabeled)'}")
            raw = _ask("  remove which number (blank to cancel): ")
            if raw.isdigit() and 1 <= int(raw) <= len(profiles):
                del profiles[int(raw) - 1]
        elif choice == "5":
            new_name = _ask(f"  new goal name (blank to keep {name!r}): ")
            if new_name:
                name = new_name
            new_type = _ask(
                f"  new augment type -- 'skills_plus' or 'regular' (blank to keep {augment_type!r}): "
            ).strip().lower()
            if new_type in ("skills_plus", "regular"):
                augment_type = new_type
        elif choice in ("0", ""):
            break
        else:
            print(f"  {choice!r} isn't one of the options above.")

    final_goal = Goal(name=name, augment_type=augment_type,
                       profiles=tuple(profiles), protected_skills=protected)
    validate_goal(final_goal)  # belt-and-suspenders, same rationale as run_wizard

    path.write_text(yaml.safe_dump(_goal_to_raw_dict(final_goal), sort_keys=False, default_flow_style=False))
    print(f"\nSaved changes to {path.resolve()}")


if __name__ == "__main__":
    run_wizard()
