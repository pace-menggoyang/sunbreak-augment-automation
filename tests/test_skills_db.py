"""Offline regression tests for skills_db.match_skill_name, covering real
OCR noise patterns observed against reference screenshots and live capture
(no game/OCR needed -- these are canned strings)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qurio_aug.skills_db import match_skill_name

# (raw OCR text, expected matched skill name or None)
CASES = [
    # Icon-bleed / prefix noise -- partial_ratio's whole reason for being.
    # Observed live: WRatio scored this 70 (just under the 75 threshold),
    # partial_ratio scores it 92.
    ("_, tune-Up", "Tune-Up"),
    ("Jiversion", "Diversion"),
    ("sritical Boost", "Critical Boost"),
    ("a Blood Awakening", "Blood Awakening"),
    ("__ Diversion", "Diversion"),
    # Character-level misreads.
    ("Weakness Expl0it", "Weakness Exploit"),
    ("Buret", "Burst"),
    ("Critical B0ost", "Critical Boost"),
    # Case inconsistency -- this font has OCR'd both a wrong-case first
    # letter and (observed live) a whole word in all caps.
    ("_. FOCUS", "Focus"),
    ("tune-Up", "Tune-Up"),
    # Garbage that must NOT confidently match anything, even though
    # partial_ratio alone would score these ~100 (a 1-2 char fragment is
    # trivially "found" inside almost any longer name) -- this is what
    # MAX_NAME_LENGTH_RATIO in skills_db.py guards against.
    ("a", None),
    ("e", None),
    ("g", None),
    ("yi", None),
    ("xyzzy nonsense", None),
    ("", None),
]


def test_match_skill_name_cases():
    failures = []
    for raw, expected in CASES:
        skill, score = match_skill_name(raw)
        got = skill.name if skill else None
        if got != expected:
            failures.append(f"{raw!r} -> {got!r} (score={score}), expected {expected!r}")
    assert not failures, "\n" + "\n".join(failures)


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
