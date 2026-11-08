"""Speed profiles: preset bundles of the three timing knobs a farming run
exposes (key hold, post-press delay, result settle delay).

Where an attempt's time goes, measured from the Windows beta logs (about
2.03s per attempt at the defaults): 7 key presses at 0.03s hold + 0.18s
delay = 1.47s (72%), the 0.5s settle before the first capture (25%), and
OCR plus everything else about 0.06s. So the delays are nearly all of it.

The two knobs are not equally risky:

- settle_delay only decides when the first screenshot is taken. If a
  sparkle hasn't faded the row reads back unreadable and the existing
  retry re-captures -- about 2.8% of first reads in the beta logs. It
  can't desync the game, so cutting it is the safe speedup.
- post_press_delay stands in for "has the menu transition settled" before
  the next key. Too short and a key lands mid-transition or is dropped,
  which fails silently. By the macro's own comments a dropped Left before
  the "Keep the previous results?" dialog would confirm the default "No"
  there, which may apply the roll that was just rejected. That is why
  the faster key delay is labelled experimental and is guarded (see
  state_machine.run: auto-slowdown up to the normal delay, and a revert to
  normal after repeated stray dialogs).

press_hold is left alone in every profile: PRESS_HOLD's own comment says
only that 30ms is comfortably above the input system's registration
threshold -- nothing was measured below it.
"""
from __future__ import annotations

from dataclasses import dataclass

from qurio_aug.input import POST_PRESS_DELAY, PRESS_HOLD
from qurio_aug.state_machine import RESULT_SETTLE_DELAY


@dataclass(frozen=True)
class SpeedProfile:
    key: str  # what --speed accepts and the menu stores
    label: str  # short, for the menu and status line
    description: str
    press_hold: float
    post_press_delay: float
    settle_delay: float
    experimental: bool = False

    @property
    def timings(self) -> tuple[float, float, float]:
        return self.press_hold, self.post_press_delay, self.settle_delay


NORMAL = SpeedProfile(
    key="normal",
    label="Normal",
    description="The tested defaults.",
    press_hold=PRESS_HOLD,
    post_press_delay=POST_PRESS_DELAY,
    settle_delay=RESULT_SETTLE_DELAY,
)

# Only the settle delay is cut: no effect on key timing, so it cannot desync
# the game. Estimated ~17% faster (2.03s -> ~1.73s per attempt).
SAFE = SpeedProfile(
    key="safe",
    label="Faster",
    description="Shorter wait before reading each roll. No change to key timing, so it can't desync the game. ~17% faster.",
    press_hold=PRESS_HOLD,
    post_press_delay=POST_PRESS_DELAY,
    settle_delay=0.2,
)

# Also shortens the delay between key presses. Estimated ~55% faster
# (2.03s -> ~1.31s per attempt). Experimental: see the module docstring.
FAST = SpeedProfile(
    key="fast",
    label="Fast (experimental)",
    description=(
        "Also shortens the delay between key presses. ~55% faster, but a key can land mid-transition. "
        "Auto-slows on stray dialogs and switches itself off after repeated ones."
    ),
    press_hold=PRESS_HOLD,
    post_press_delay=0.12,
    settle_delay=0.2,
    experimental=True,
)

PROFILES: dict[str, SpeedProfile] = {p.key: p for p in (NORMAL, SAFE, FAST)}

def get_profile(key: str | None) -> SpeedProfile:
    """The profile for `key`; unknown or missing falls back to NORMAL."""
    return PROFILES.get(key or "normal", NORMAL)
