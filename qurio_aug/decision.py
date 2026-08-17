"""Accept/reject decision engine for a parsed augmentation roll.

Deliberately ignores defense/resistance/slot changes -- only skill deltas
factor into the decision. A Goal is one or more acceptance Profiles
(accepted if ANY profile matches -- OR semantics), plus a set of
protected skills that's checked globally, before any profile:

1. Reject if any protected skill lost a level (or was fully removed /
   "None"), regardless of which profile might otherwise match.
2. For each profile, in order:
   a. Reject this profile if any of its required_skills isn't present
      with delta >= min_level.
   b. Reject this profile if any OTHER skill gain isn't in that profile's
      allowed_additional_skills pool.
   c. Reject this profile if it needs at least min_additional_skills
      gains from that pool and doesn't have enough.
   d. Otherwise this profile matches -> accept the roll.
3. If no profile matched, reject.

Skill LOSSES on non-protected skills are never a rejection reason -- only
protected-skill losses and gains outside a profile's allowed pool matter.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# A single Skill+/Slot+ pool roll is always worth exactly 1 level per the
# datamine (Lv1=1 for every such pool entry); the same skill stacking
# across several of the ~6-7 rolls in one augmentation can push a delta
# higher, but budget/cost caps make that increasingly implausible past a
# few levels. |delta| at or above this is flagged as suspicious (see
# logger.py) rather than silently trusted or rejected -- OCR digit misreads
# are a real residual risk (see ocr.py's _ocr_single_digit docstring) that
# can't be fully eliminated, so this is a visibility net for manual review
# (especially during --dry-run validation) rather than an automatic guard.
SUSPICIOUS_DELTA_THRESHOLD = 4


# A skill row read off the Augmented Status panel. `delta` is the signed
# level change; a fully-removed skill ("None" in red) is represented as
# `removed=True` with delta left at 0 (we don't know how many levels were
# lost, just that the skill is gone).
@dataclass(frozen=True)
class SkillResult:
    name: str
    delta: int = 0
    removed: bool = False

    @property
    def is_gain(self) -> bool:
        return not self.removed and self.delta > 0

    @property
    def is_loss(self) -> bool:
        return self.removed or self.delta < 0

    @property
    def is_suspicious(self) -> bool:
        return abs(self.delta) >= SUSPICIOUS_DELTA_THRESHOLD


@dataclass(frozen=True)
class RequiredSkill:
    name: str
    min_level: int = 1


@dataclass(frozen=True)
class Profile:
    required_skills: tuple[RequiredSkill, ...]
    allowed_additional_skills: frozenset[str] = field(default_factory=frozenset)
    min_additional_skills: int = 0
    name: str = ""  # optional label, for clearer decision reasons/logs


@dataclass(frozen=True)
class Goal:
    name: str
    augment_type: str  # "skills_plus" | "regular" -- logging metadata only
    profiles: tuple[Profile, ...]
    protected_skills: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class Decision:
    accepted: bool
    reason: str
    added: list[SkillResult]
    removed: list[SkillResult]


def _evaluate_profile(profile: Profile, added: list[SkillResult]) -> tuple[bool, str]:
    label = profile.name or "profile"
    gained_level = {r.name: r.delta for r in added}

    for req in profile.required_skills:
        got = gained_level.get(req.name, 0)
        if got < req.min_level:
            return False, f"{label}: '{req.name}' not met (got +{got}, need +{req.min_level})"

    required_names = {r.name for r in profile.required_skills}
    extra_gains = [r for r in added if r.name not in required_names]

    for r in extra_gains:
        if r.name not in profile.allowed_additional_skills:
            return False, f"{label}: unexpected gain '{r.name}' not in allowed pool"

    if len(extra_gains) < profile.min_additional_skills:
        return False, (
            f"{label}: only {len(extra_gains)} additional skill(s) "
            f"(need >= {profile.min_additional_skills})"
        )

    names = ", ".join(f"{req.name} +{gained_level[req.name]}" for req in profile.required_skills)
    extra = ", ".join(f"{r.name} +{r.delta}" for r in extra_gains)
    detail = f"{names}" + (f" + [{extra}]" if extra else "")
    return True, f"{label} matched ({detail})"


def any_profile_reachable(profiles: tuple[Profile, ...], gains: list[SkillResult]) -> bool:
    """Conservative pre-check usable before all data is available (e.g.
    before reading further pages of the game's UI): does `gains` alone
    still leave *any* profile able to match?

    Only checks each profile's required_skills -- not the allowed pool or
    min_additional_skills, which can only be fully verified once every
    gain is known. That makes this a one-directional check: False means
    "definitely can't match, full stop" (every profile is missing a
    required skill that nothing still unseen could add, since gains don't
    materialize from nowhere); True means "still possible", not "will
    accept" -- the real evaluate() call is what actually decides that.
    Never a false negative, so it's safe to use as an early-exit gate.
    """
    gained_level = {r.name: r.delta for r in gains if r.is_gain}
    for profile in profiles:
        if all(gained_level.get(req.name, 0) >= req.min_level for req in profile.required_skills):
            return True
    return False


def evaluate(goal: Goal, roll: list[SkillResult]) -> Decision:
    added = [r for r in roll if r.is_gain]
    removed = [r for r in roll if r.is_loss]

    for r in removed:
        if r.name in goal.protected_skills:
            detail = "removed entirely" if r.removed else f"Lv {r.delta}"
            return Decision(
                False,
                f"protected skill '{r.name}' was touched ({detail})",
                added,
                removed,
            )

    failures = []
    for profile in goal.profiles:
        matched, reason = _evaluate_profile(profile, added)
        if matched:
            return Decision(True, reason, added, removed)
        failures.append(reason)

    return Decision(False, "; ".join(failures), added, removed)
