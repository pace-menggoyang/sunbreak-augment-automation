"""Global start/force-stop hotkeys, so you don't have to race a countdown
to switch to the game window, and have a way to interrupt a long run
without switching focus away from the game to kill the terminal.

Uses pynput.keyboard.GlobalHotKeys, which listens system-wide regardless
of which window is focused -- the same macOS Accessibility permission
already required for sending input (see input.py) covers this too, no
extra grant needed.

Defaults to Control(⌃)+M (start) / Control(⌃)+N (stop) on macOS rather
than function keys: many Mac keyboards map F-keys to brightness/volume/
Mission Control by default, needing an extra Fn press to get the literal
F-key -- not "reachable" at all. Control+letter needs no Fn and is one
comfortable one-handed reach.

M/N specifically (not, say, S/X) because pynput's listener observes key
events globally but doesn't consume/suppress them -- the raw keydown
still reaches whichever app is focused underneath it. Confirmed live on
macOS: Option+S/Option+X leaked through as plain S/X to the game (which
doesn't check modifier state for its own bindings), triggering in-game
navigate-down/autoselect instead of just registering as our hotkey. The
game binds confirm=F, cancel=Esc, nav=WASD, autoselect=X, pagination=Q/E
-- M and N aren't used for anything, so even if a keydown leaks through
here too, it's a no-op in-game rather than an unwanted action.

Control, not Option, as the modifier on macOS: Option is macOS's
system-wide modifier for typing accented/special characters (Option+M
literally types "µ"), and pynput resolves keys on macOS by the
*character* an event produces after that substitution -- so Option+M
showed up to our listener as "µ", not "m", and never matched the
registered "<alt>+m" combo at all (confirmed live: it printed "µ" into
the terminal instead of firing). Control doesn't participate in that
substitution table, so Control+letter resolves to the plain letter and
matches reliably.

Windows uses Alt(+M)/Alt(+N) instead of Control -- a *different*
same-shape collision, found on first real Windows hardware testing (see
docs/windows-beta-checklist.md): Sunbreak's default Windows control
scheme binds bare Ctrl to "Skill Info" and bare Shift to "Compare
Equipment" on the Augmentation Results screen itself (visible in its own
on-screen button-hint bar), and per the same non-suppressing-listener
mechanism above, holding Ctrl (regardless of M) reaches the focused game
and pops that overlay over the screen this tool is about to read --
confirmed live: a dry-run's screenshot came back with the Skill Info
panel visibly covering the results. Alt isn't in that hint bar and isn't
Windows' macOS-style accent-substitution modifier, so it doesn't have
either collision there. Kept per-platform (not just switched everywhere)
because Control+M/N *is* already proven collision-free in production on
macOS -- see above.
"""
from __future__ import annotations

import sys
import threading

from pynput import keyboard

if sys.platform == "win32":
    DEFAULT_START_HOTKEY = "<alt>+m"
    DEFAULT_STOP_HOTKEY = "<alt>+n"
else:
    DEFAULT_START_HOTKEY = "<ctrl>+m"
    DEFAULT_STOP_HOTKEY = "<ctrl>+n"


class StopRequested(Exception):
    """Raised to unwind out of a macro/loop cleanly when the stop hotkey
    fires -- caught at the top of state_machine.run() to exit gracefully
    rather than mid-keypress.
    """


class HotkeyController:
    def __init__(self, start_hotkey: str = DEFAULT_START_HOTKEY, stop_hotkey: str = DEFAULT_STOP_HOTKEY):
        self.start_hotkey = start_hotkey
        self.stop_hotkey = stop_hotkey
        self._start_event = threading.Event()
        self._stop_event = threading.Event()
        self._listener = keyboard.GlobalHotKeys({
            start_hotkey: self._start_event.set,
            stop_hotkey: self._stop_event.set,
        })

    def __enter__(self) -> "HotkeyController":
        self._listener.start()
        self._listener.wait()  # block until the listener thread is actually up
        return self

    def __exit__(self, *exc_info) -> None:
        self._listener.stop()

    def wait_for_start(self) -> None:
        """Blocks until the start hotkey fires. Consumes the event, so a
        second wait_for_start() call (e.g. before the next armor piece)
        needs a fresh press.
        """
        self._start_event.wait()
        self._start_event.clear()

    def stop_requested(self) -> bool:
        return self._stop_event.is_set()

    def reset_stop(self) -> None:
        self._stop_event.clear()
