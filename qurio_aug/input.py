"""Keyboard input simulation for the confirmed in-game bindings + the two
end-to-end macros derived from step-references/*.png:

  general:    confirm=F   cancel=Esc   navigate=WASD
  material select:  autoselect=X
  skill pagination:  prev_page=Q  next_page=E

State machine (see the plan for the full STATE1..6 walkthrough):
  reroll_macro: Esc, A, F, F, X, F, F  -- reject this roll, spend mats, get a new one
  accept_macro: F, A, F, D, F          -- apply this roll, stop augmenting this piece

Requires the macOS Accessibility permission for the process running this
(System Settings > Privacy & Security > Accessibility) -- pynput raises
no clear error if it's missing, it just silently fails to deliver events,
so this module can't detect that condition itself. If macros run but
nothing happens in-game, check Accessibility first.
"""
from __future__ import annotations

import time
from typing import Callable

from pynput.keyboard import Controller, Key
from pynput.mouse import Controller as MouseController

from qurio_aug.hotkeys import StopRequested

# Fraction of the window's (x, y, width, height) bounds to park the mouse
# cursor at before each capture -- bottom-left corner, well clear of the
# Current Status / Augmented Status / armor preview panels (which span
# roughly the horizontal middle-to-right of the window). A stray cursor
# sitting over skill text reliably corrupts OCR (observed live: "Razor
# Sharp" read as garbage with the cursor arrow overlapping it) and, unlike
# the sparkle decoration, won't move on its own between retries -- only
# relocating it actually fixes this.
MOUSE_PARK_FRACTION = (0.02, 0.97)

# Seconds to hold a key down, and to wait after releasing before the next
# action. PRESS_HOLD mainly needs to clear the input system's own
# registration threshold (comfortably true even at 30ms); POST_PRESS_DELAY
# is the real risk lever -- it's standing in for "how long until the
# game's menu transition/animation has settled enough for the next input
# to land on the intended screen", which isn't something OCR or any other
# feedback signal here confirms before the next key fires. Cut too far and
# a macro's later presses could register mid-transition, landing on the
# wrong option or getting silently dropped -- a state desync that (unlike
# an OCR misread) fails silently rather than raising. These were halved
# from an earlier, more conservative baseline as a deliberate middle
# ground, not a measured safe minimum -- validate at a lower --max-attempts
# before trusting a long run, and back off if anything looks like it
# landed on the wrong screen (see main.py --press-hold / --post-press-delay
# to tune without editing code).
PRESS_HOLD = 0.03
POST_PRESS_DELAY = 0.18

_KEY_MAP = {
    "confirm": "f",
    "cancel": Key.esc,
    "up": "w",
    "down": "s",
    "left": "a",
    "right": "d",
    "autoselect": "x",
    "prev_page": "q",
    "next_page": "e",
}


class GameInput:
    def __init__(self, controller: Controller | None = None,
                 post_press_delay: float = POST_PRESS_DELAY,
                 press_hold: float = PRESS_HOLD,
                 mouse: MouseController | None = None,
                 should_stop: Callable[[], bool] | None = None):
        self._controller = controller or Controller()
        self._post_press_delay = post_press_delay
        self._press_hold = press_hold
        self._mouse = mouse or MouseController()
        # Checked before every keypress -- the finest-grained point to
        # catch a force-stop hotkey (see hotkeys.py), so a macro unwinds
        # within one press's timing budget rather than running to
        # completion first.
        self._should_stop = should_stop or (lambda: False)

    def park_mouse(self, window_bounds: tuple[float, float, float, float]) -> None:
        """Move the cursor to a safe corner of the game window, clear of
        the Augmentation Results panels, so it can't be sitting over skill
        text at capture time. Cheap enough to call before every capture.
        """
        x, y, width, height = window_bounds
        fx, fy = MOUSE_PARK_FRACTION
        self._mouse.position = (x + width * fx, y + height * fy)

    def press(self, action: str) -> None:
        if self._should_stop():
            raise StopRequested()
        key = _KEY_MAP[action]
        self._controller.press(key)
        time.sleep(self._press_hold)
        self._controller.release(key)
        time.sleep(self._post_press_delay)

    def confirm(self) -> None:
        self.press("confirm")

    def cancel(self) -> None:
        self.press("cancel")

    def nav_left(self) -> None:
        self.press("left")

    def nav_right(self) -> None:
        self.press("right")

    def autoselect_materials(self) -> None:
        self.press("autoselect")

    def next_page(self) -> None:
        self.press("next_page")

    def prev_page(self) -> None:
        self.press("prev_page")

    def trigger_roll(self) -> None:
        """STATE1 (Material Select) -> STATE2 -> STATE3 -> fresh STATE4 roll."""
        self.autoselect_materials()  # STATE1 -> STATE2 (materials filled, Confirm focused)
        self.confirm()                # STATE2 -> STATE3 "Requires materials..." (default Yes)
        self.confirm()                # confirm -> fresh STATE4 roll

    def reroll_macro(self) -> None:
        """STATE4 (reject) -> STATE5b -> STATE6b -> STATE1 -> ... -> fresh STATE4."""
        self.cancel()             # STATE4 -> STATE5b "Keep the previous results?" (default No)
        self.nav_left()           # -> Yes
        self.confirm()            # confirm Yes (keep previous / discard this roll) -> STATE6b
        self.confirm()            # STATE6b "Continue Augmenting?" (default Yes) -> STATE1
        self.trigger_roll()       # STATE1 -> ... -> fresh STATE4

    def accept_macro(self) -> None:
        """STATE4 (accept) -> STATE5a -> STATE6a -> exits to Smithy menu."""
        self.confirm()            # STATE4 -> STATE5a "Apply results?" (default No)
        self.nav_left()           # -> Yes
        self.confirm()            # confirm Yes (apply) -> STATE6a
        self.nav_right()          # STATE6a "Continue Augmenting?" (default Yes) -> No
        self.confirm()            # confirm No -> exits to Smithy menu, done
