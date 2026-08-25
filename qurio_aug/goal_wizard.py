"""Interactive CLI wizard that asks a series of questions and writes a
goal YAML matching goal_config.py's schema -- for people who'd rather
answer prompts than hand-write YAML from scratch. Every skill name typed
anywhere in the flow goes through the same fuzzy-match helper already
used to correct OCR noise (skills_db.match_skill_name), so a typo gets
caught and offered a correction live, instead of only surfacing later via
goal_config.validate_goal (which still runs too, as a final check, since
a wizard bug shouldn't be trusted any more than a hand-edited file's typo
would be).

All choice-menus go through tui.py's arrow-key widget (tui.pick/
pick_index/ask_yes_no), the same one main.py's top-level menu uses, so
this looks and behaves consistently with the rest of the app. Every one
of those calls uses tui's default on_ctrl_c="raise": Ctrl+C anywhere in
here still aborts the whole wizard/editor (propagates as a real
KeyboardInterrupt up to main.py's top-level catch), exactly like it did
when this was all plain input() -- a menu's own Escape key still just
backs out of that one menu/step, not the whole flow.

Run via `python -m qurio_aug.main --wizard` (see main.py).
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from qurio_aug import skills_db, tui
from qurio_aug.goal_config import validate_goal
from qurio_aug.decision import Goal, Profile, RequiredSkill

GOALS_DIR = Path("goals")

_AUGMENT_TYPES: list[tuple[str, str]] = [
    ("skills_plus", "skills_plus (gain/lose skill levels)"),
    ("regular", "regular (Defense/Slots/Resistance)"),
]


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
            if tui.ask_yes_no(f"    '{raw}' isn't an exact match -- did you mean '{skill.name}'?", default=True):
                return skill.name
            raw = tui.ask_text("    type it again: ")
            continue
        if skill is not None:
            return skill.name
        if tui.ask_yes_no(f"    '{raw}' isn't a recognized skill and no close match was found -- use it anyway?"):
            return raw
        raw = tui.ask_text("    type it again: ")


def _ask_skill_list(prompt_label: str) -> list[str]:
    tui.print_header(f"{prompt_label} (enter one at a time, blank line when done):")
    names = []
    all_names = skills_db.all_skill_names()
    while True:
        raw = tui.ask_text_with_completion(f"  skill {len(names) + 1} (or blank to finish): ", all_names)
        if not raw:
            break
        names.append(_resolve_skill_name(raw))
    return names


def _ask_profile(index: int) -> Profile:
    tui.print_header(f"--- Profile {index} ---")
    label = tui.ask_text("Optional label for this profile (blank to skip): ")

    required_names = []
    tui.print_header("Required skills for this profile (at least one, blank line when done):")
    all_names = skills_db.all_skill_names()
    while True:
        raw = tui.ask_text_with_completion(
            f"  required skill {len(required_names) + 1} (or blank to finish): ", all_names,
        )
        if not raw:
            if required_names:
                break
            print(tui.warn("  a profile needs at least one required skill."))
            continue
        required_names.append(_resolve_skill_name(raw))

    required = []
    for name in required_names:
        min_level = tui.ask_int(f"  minimum level for '{name}'", default=1)
        required.append(RequiredSkill(name=name, min_level=min_level))

    allowed = _ask_skill_list("Additional skills that are OK to gain alongside the required ones (optional)")
    min_additional = 0
    if allowed:
        min_additional = tui.ask_int("Minimum number of those additional skills required", default=0)

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
    tui.print_header("Qurio augmentation goal wizard")
    print("Answers build a goal YAML you can review/edit afterward. Ctrl+C at any point cancels.")

    name = tui.ask_text("Goal name (used in logs and the output filename): ")
    while not name:
        name = tui.ask_text("Goal name can't be blank: ")

    augment_type = tui.pick("Augment type (must match what you select in-game)", _AUGMENT_TYPES)
    while augment_type is None:
        augment_type = tui.pick("Augment type is required -- pick one", _AUGMENT_TYPES)

    protected = _ask_skill_list(
        "Protected skills -- reject any roll that loses or fully removes "
        "these, no matter what else it does (optional)"
    )

    profiles = []
    i = 1
    while True:
        profiles.append(_ask_profile(i))
        i += 1
        if not tui.ask_yes_no("Add another profile (roll accepted if ANY profile matches)?"):
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

    tui.print_success(f"\nWrote {out_path.resolve()}")
    print(f"Next: python -m qurio_aug.main --goal {out_path} --dry-run")
    return out_path


def _display_goal(goal: Goal) -> list[tuple[str, str]]:
    """Colored fragments describing `goal` -- passed as `body=` to
    run_editor's top-level tui.pick call, mirroring the main REPL menu's
    own "status above the command list" layout instead of printing the
    summary separately before a distinct menu.
    """
    frags: list[tuple[str, str]] = [
        ("class:status-neutral", "name: "), ("", f"{goal.name}\n"),
        ("class:status-neutral", "augment_type: "), ("", f"{goal.augment_type}\n"),
        ("class:status-neutral", "protected_skills: "),
        ("", (", ".join(sorted(goal.protected_skills)) or "(none)") + "\n"),
    ]
    for i, p in enumerate(goal.profiles, 1):
        label = f" ({p.name})" if p.name else ""
        frags.append(("class:status-neutral", f"profile {i}{label}:\n"))
        for r in p.required_skills:
            frags.append(("", f"  required: {r.name} (min level {r.min_level})\n"))
        if p.allowed_additional_skills:
            frags.append(("", f"  allowed additional: {', '.join(sorted(p.allowed_additional_skills))} "
                               f"(min {p.min_additional_skills} required)\n"))
    return frags


def _edit_protected_skills(protected: frozenset[str]) -> frozenset[str]:
    current = set(protected)
    while True:
        body = [("", "Protected skills: " + (", ".join(sorted(current)) or "(none)") + "\n")]
        choice = tui.pick("Edit protected skills", [
            ("add", "Add a skill"),
            ("remove", "Remove a skill"),
            ("done", "Done"),
        ], body=body)
        if choice == "add":
            name = tui.ask_text_with_completion("  skill to protect: ", skills_db.all_skill_names())
            if name:
                current.add(_resolve_skill_name(name))
        elif choice == "remove":
            if not current:
                print(tui.warn("  nothing to remove."))
                continue
            names = sorted(current)
            idx = tui.pick_index("Remove which skill?", names)
            if idx is not None:
                current.discard(names[idx])
        elif choice in ("done", None):
            return frozenset(current)


def _edit_profile(profile: Profile) -> Profile:
    required = list(profile.required_skills)
    allowed = set(profile.allowed_additional_skills)
    min_additional = profile.min_additional_skills
    label = profile.name

    while True:
        title = f"Editing profile{f' ({label})' if label else ''}"
        body: list[tuple[str, str]] = []
        for i, r in enumerate(required, 1):
            body.append(("", f"  {i}. {r.name} (min level {r.min_level})\n"))
        body.append(("", "  allowed additional: " + (", ".join(sorted(allowed)) or "(none)")
                     + (f" (min {min_additional} required)" if allowed else "") + "\n"))

        choice = tui.pick(title, [
            ("add_required", "Add a required skill"),
            ("remove_required", "Remove a required skill"),
            ("change_level", "Change a required skill's minimum level"),
            ("add_allowed", "Add an allowed additional skill"),
            ("remove_allowed", "Remove an allowed additional skill"),
            ("change_min_additional", "Change minimum additional skills required"),
            ("rename", "Rename this profile's label"),
            ("done", "Done editing this profile"),
        ], body=body)

        if choice == "add_required":
            name = tui.ask_text_with_completion("  skill name: ", skills_db.all_skill_names())
            if name:
                name = _resolve_skill_name(name)
                level = tui.ask_int(f"  minimum level for '{name}'", default=1)
                required.append(RequiredSkill(name=name, min_level=level))
        elif choice == "remove_required":
            if not required:
                print(tui.warn("  nothing to remove."))
                continue
            idx = tui.pick_index("Remove which required skill?", [r.name for r in required])
            if idx is not None:
                del required[idx]
        elif choice == "change_level":
            if not required:
                print(tui.warn("  nothing to change."))
                continue
            labels = [f"{r.name} (currently min level {r.min_level})" for r in required]
            idx = tui.pick_index("Change which required skill's level?", labels)
            if idx is not None:
                new_level = tui.ask_int(f"  new minimum level for '{required[idx].name}'",
                                         default=required[idx].min_level)
                required[idx] = RequiredSkill(name=required[idx].name, min_level=new_level)
        elif choice == "add_allowed":
            name = tui.ask_text_with_completion("  skill name: ", skills_db.all_skill_names())
            if name:
                allowed.add(_resolve_skill_name(name))
        elif choice == "remove_allowed":
            if not allowed:
                print(tui.warn("  nothing to remove."))
                continue
            names = sorted(allowed)
            idx = tui.pick_index("Remove which allowed skill?", names)
            if idx is not None:
                allowed.discard(names[idx])
        elif choice == "change_min_additional":
            min_additional = tui.ask_int("  minimum additional skills required", default=min_additional)
        elif choice == "rename":
            label = tui.ask_text("  new label (blank to clear): ")
        elif choice in ("done", None):
            if not required:
                print(tui.warn("  a profile needs at least one required skill -- add one before finishing."))
                continue
            return Profile(
                required_skills=tuple(required),
                allowed_additional_skills=frozenset(allowed),
                min_additional_skills=min_additional,
                name=label,
            )


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

    tui.print_header(f"Editing {path} -- Ctrl+C at any point discards changes.")
    while True:
        current_goal = Goal(name=name, augment_type=augment_type,
                             profiles=tuple(profiles), protected_skills=protected)
        choice = tui.pick(f"Editing {name}", [
            ("edit_protected", "Edit protected skills"),
            ("edit_profile", "Edit an existing profile"),
            ("add_profile", "Add a new profile"),
            ("remove_profile", "Remove a profile"),
            ("rename", "Rename goal / change augment type"),
            ("save", "Save and exit"),
        ], body=_display_goal(current_goal))

        if choice == "edit_protected":
            protected = _edit_protected_skills(protected)
        elif choice == "edit_profile":
            if not profiles:
                print(tui.warn("  no profiles to edit."))
                continue
            labels = [p.name or "(unlabeled)" for p in profiles]
            idx = tui.pick_index("Edit which profile?", labels)
            if idx is not None:
                profiles[idx] = _edit_profile(profiles[idx])
        elif choice == "add_profile":
            profiles.append(_ask_profile(len(profiles) + 1))
        elif choice == "remove_profile":
            if not profiles:
                print(tui.warn("  no profiles to remove."))
                continue
            if len(profiles) == 1:
                print(tui.warn("  a goal needs at least one profile -- add another before removing this one."))
                continue
            labels = [p.name or "(unlabeled)" for p in profiles]
            idx = tui.pick_index("Remove which profile?", labels)
            if idx is not None:
                del profiles[idx]
        elif choice == "rename":
            new_name = tui.ask_text(f"  new goal name (blank to keep {name!r}): ")
            if new_name:
                name = new_name
            new_type = tui.pick("  new augment type (Esc to keep current)", _AUGMENT_TYPES, default=augment_type)
            if new_type is not None:
                augment_type = new_type
        elif choice in ("save", None):
            break

    final_goal = Goal(name=name, augment_type=augment_type,
                       profiles=tuple(profiles), protected_skills=protected)
    validate_goal(final_goal)  # belt-and-suspenders, same rationale as run_wizard

    path.write_text(yaml.safe_dump(_goal_to_raw_dict(final_goal), sort_keys=False, default_flow_style=False))
    tui.print_success(f"\nSaved changes to {path.resolve()}")


if __name__ == "__main__":
    tui.enable_windows_ansi_colors()  # normally main.py's job -- this is the standalone entry point
    run_wizard()
