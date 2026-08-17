"""Canonical skill list + fuzzy name matching to correct OCR noise."""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache

from rapidfuzz import fuzz, process

from qurio_aug.paths import resource_dir

DATA_DIR = resource_dir("data")

# Below this score (0-100), we don't trust the fuzzy match at all -- treat
# the row as unparseable and let the caller fail safe (reject/retry).
MIN_MATCH_SCORE = 75

# The matched skill name can't be more than this many times longer than the
# OCR'd text -- guards against fuzz.partial_ratio's main failure mode: a
# short fragment (garbage or an icon-bleed sliver) scoring ~100 against a
# long name just because it happens to appear as a substring somewhere in
# it (e.g. a single "a" scores 100 against "Adrenaline Rush"). Legitimate
# OCR noise (extra icon-bleed characters, wrong case, a stray "_") makes the
# raw text a bit *longer* than the true name, not dramatically shorter.
MAX_NAME_LENGTH_RATIO = 2.0


@dataclass(frozen=True)
class Skill:
    skill_id: int | None  # None for skills added via data/skills_overrides.json
    name: str
    cost: int | None  # None if unknown -- unused by decision.py, name-matching only


@lru_cache(maxsize=1)
def _load_skills() -> tuple[Skill, ...]:
    raw = json.loads((DATA_DIR / "skills.json").read_text())
    return tuple(Skill(s["skill_id"], s["name"], s["cost"]) for s in raw)


@lru_cache(maxsize=1)
def _name_index() -> dict[str, Skill]:
    return {s.name: s for s in _load_skills()}


def all_skill_names() -> list[str]:
    return [s.name for s in _load_skills()]


def match_skill_name(raw_text: str) -> tuple[Skill | None, int]:
    """Fuzzy-match OCR'd text against the canonical skill list.

    Uses partial_ratio (best-matching substring) rather than a whole-string
    ratio, because OCR noise here is usually *extra* characters bleeding in
    from the skill icon to the left of the name (e.g. "_, tune-Up" for
    "Tune-Up") -- a whole-string comparison penalizes that prefix noise
    enough to occasionally miss a real, legible match (observed live:
    "_, tune-Up" scored 70 against "Tune-Up" with WRatio, under the 75
    threshold, vs 92 with partial_ratio). See MAX_NAME_LENGTH_RATIO for
    the guard against partial_ratio's own false-positive mode.

    Case-insensitive: this game's font has OCR'd inconsistently on case
    more than once (a lowercase first letter where it should be capital,
    and separately a whole word read fully upper-case -- "FOCUS" for
    "Focus", which scored only 22 against the correctly-cased name since
    rapidfuzz's scorers are case-sensitive by default). The skill name
    stays properly-cased in what's returned; only the comparison ignores it.

    Returns (Skill, score) or (None, 0) if nothing clears MIN_MATCH_SCORE
    or the length-ratio guard.
    """
    raw_text = raw_text.strip()
    if not raw_text:
        return None, 0

    names = all_skill_names()
    result = process.extractOne(raw_text, names, scorer=fuzz.partial_ratio, processor=str.lower)
    if result is None:
        return None, 0

    name, score, _ = result
    if score < MIN_MATCH_SCORE:
        return None, int(score)
    if len(name) > len(raw_text) * MAX_NAME_LENGTH_RATIO:
        return None, int(score)
    return _name_index()[name], int(score)
