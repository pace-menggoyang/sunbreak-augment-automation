"""CLI entry point.

Usage:
  # Don't have a goal config yet? Build one interactively:
  python -m qurio_aug.main --wizard

  # Validate OCR + decision logic against a roll you triggered manually
  # in-game, one call per roll. Never sends the accept/reject/reroll
  # macros (won't change game state) but may send Q/E to page through a
  # multi-page roll while reading it.
  python -m qurio_aug.main --goal configs/goals/example.yaml --dry-run

  # Full autonomous loop: assumes the game is already sitting at STATE 1
  # (Material Select, correct armor piece + augment type chosen).
  python -m qurio_aug.main --goal configs/goals/example.yaml

By default, both modes wait for a start hotkey (Control+M) rather than a
timed countdown -- position the game window and get to the right screen
with no time pressure, then press Control+M when ready. Control+N
force-stops a running loop at any point (checked before every keypress
and during every wait), without needing to switch focus back to the
terminal. Pass --no-hotkeys to fall back to the old fixed countdown
instead.
"""
from __future__ import annotations

import argparse
import sys
import time

from qurio_aug import capture, ocr, state_machine
from qurio_aug.goal_config import GoalValidationError, load_goal
from qurio_aug.goal_wizard import run_wizard
from qurio_aug.hotkeys import DEFAULT_START_HOTKEY, DEFAULT_STOP_HOTKEY, HotkeyController
from qurio_aug.input import POST_PRESS_DELAY, PRESS_HOLD
from qurio_aug.logger import AttemptLogger
from qurio_aug.tesseract_setup import configure_tesseract, selfcheck

# Fallback for --no-hotkeys. Both --dry-run (which may send Q/E to page
# through a multi-page roll) and the full run send real keystrokes via
# pynput, which only reach the game if it's the focused window -- not the
# terminal this was launched from. This grace period gives you time to
# Alt/Cmd-Tab over after hitting Enter.
DEFAULT_START_DELAY = 3.0


_MODIFIER_DISPLAY_NAMES = {
    "alt": "Option",
    "cmd": "Command",
    "ctrl": "Control",
    "shift": "Shift",
}


def _format_hotkey(hotkey: str) -> str:
    """"<alt>+s" -> "Option+S" -- the pynput hotkey string is what
    HotkeyController needs, but not what you want to read in a prompt.
    """
    parts = hotkey.split("+")
    display = []
    for part in parts:
        if part.startswith("<") and part.endswith(">"):
            name = part[1:-1]
            display.append(_MODIFIER_DISPLAY_NAMES.get(name, name.capitalize()))
        else:
            display.append(part.upper())
    return "+".join(display)


def _run_selfcheck() -> None:
    """Prints enough to diagnose "it doesn't work" from a single command's
    output -- the thing to ask a beta tester to run and paste, especially
    on a compiled build where there's no source to poke through.
    """
    print(f"platform: {sys.platform}")
    print(f"capture backend: {capture._backend.__name__}")
    try:
        version = selfcheck()
        print(f"tesseract: OK (version {version})")
    except RuntimeError as e:
        print(f"tesseract: FAILED -- {e}", file=sys.stderr)
        sys.exit(1)
    windows = capture.find_windows("")
    print(f"visible windows: {len(windows)} (run --list-windows to see them all)")


def _list_windows() -> None:
    windows = capture.find_windows("")
    if not windows:
        print("no visible windows found")
        return
    for w in windows:
        print(f"owner={w.owner_name!r} title={w.title!r} bounds={w.bounds}")


def _countdown(seconds: float) -> None:
    if seconds <= 0:
        return
    print(f"switch to the game window now -- sending input in {seconds:.0f}s", end="", flush=True)
    remaining = seconds
    step = 1.0
    while remaining > 0:
        time.sleep(min(step, remaining))
        remaining -= step
        print(f" {max(remaining, 0):.0f}...", end="", flush=True)
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    goal_source = parser.add_mutually_exclusive_group()
    goal_source.add_argument("--goal", help="path to a goal YAML config")
    goal_source.add_argument(
        "--wizard", action="store_true",
        help="run an interactive wizard to build a goal YAML, then exit "
        "(doesn't chain into a run -- re-invoke with --goal afterward)",
    )
    parser.add_argument(
        "--selfcheck", action="store_true",
        help="check that the OCR engine and window-capture backend are working, "
        "print the results, and exit -- run this first if something doesn't "
        "work and you're not sure why (especially on a compiled build)",
    )
    parser.add_argument(
        "--list-windows", action="store_true",
        help="print every visible window's owner/title/bounds and exit -- use "
        "this to find the right --window value if the game window isn't "
        "being found automatically",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="evaluate the roll currently on screen and exit, without accepting/"
        "rejecting/rerolling (may still send Q/E to page through a multi-page roll)",
    )
    parser.add_argument(
        "--window",
        default=None,
        help="override the window title/owner hint from regions.yaml",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=state_machine.MAX_ATTEMPTS_DEFAULT,
        help="safety cap on reroll attempts before giving up",
    )
    parser.add_argument(
        "--no-hotkeys",
        action="store_true",
        help="fall back to a fixed --start-delay countdown instead of waiting for "
        "the start hotkey (and disables the force-stop hotkey entirely)",
    )
    parser.add_argument(
        "--start-hotkey",
        default=DEFAULT_START_HOTKEY,
        help=f"pynput hotkey string that starts the run (default {DEFAULT_START_HOTKEY!r})",
    )
    parser.add_argument(
        "--stop-hotkey",
        default=DEFAULT_STOP_HOTKEY,
        help=f"pynput hotkey string that force-stops a running loop (default {DEFAULT_STOP_HOTKEY!r})",
    )
    parser.add_argument(
        "--start-delay",
        type=float,
        default=DEFAULT_START_DELAY,
        help=f"(--no-hotkeys only) seconds to wait before sending any input, so "
        f"you can switch to the game window (default {DEFAULT_START_DELAY}; 0 to skip)",
    )
    parser.add_argument(
        "--press-hold",
        type=float,
        default=PRESS_HOLD,
        help=f"seconds to hold each key down (default {PRESS_HOLD}). Low risk to "
        f"lower -- mainly needs to clear the input system's registration threshold.",
    )
    parser.add_argument(
        "--post-press-delay",
        type=float,
        default=POST_PRESS_DELAY,
        help=f"seconds to wait after each key before the next one (default "
        f"{POST_PRESS_DELAY}). This is standing in for menu transition time -- "
        f"cutting it too far risks a later keypress in a macro landing mid-"
        f"transition on the wrong screen, which fails silently rather than "
        f"raising. Validate at a low --max-attempts before trusting a lower "
        f"value for a long run.",
    )
    parser.add_argument(
        "--settle-delay",
        type=float,
        default=state_machine.RESULT_SETTLE_DELAY,
        help=f"seconds to wait after a roll lands before the first capture, for "
        f"the sparkle decoration to fade (default {state_machine.RESULT_SETTLE_DELAY}). "
        f"Lower-risk to cut than the press delays -- worst case is one extra "
        f"retry, not a state desync.",
    )
    args = parser.parse_args()
    configure_tesseract()

    if args.selfcheck:
        _run_selfcheck()
        return

    if args.list_windows:
        _list_windows()
        return

    if args.wizard:
        run_wizard()
        return

    if not args.goal:
        parser.error("one of --goal, --wizard, --selfcheck, or --list-windows is required")

    try:
        goal = load_goal(args.goal)
    except GoalValidationError as e:
        print(f"goal config problem in {args.goal}:\n{e}", file=sys.stderr)
        sys.exit(2)
    region_config = ocr.load_region_config()

    common_kwargs = dict(
        window_title_hint=args.window,
        region_config=region_config,
        press_hold=args.press_hold,
        post_press_delay=args.post_press_delay,
        settle_delay=args.settle_delay,
    )

    if args.no_hotkeys:
        _countdown(args.start_delay)
        _run(args, goal, common_kwargs, should_stop=lambda: False)
        return

    with HotkeyController(args.start_hotkey, args.stop_hotkey) as hotkeys:
        mode = "dry-run evaluation" if args.dry_run else "autonomous loop"
        start_display = _format_hotkey(args.start_hotkey)
        stop_display = _format_hotkey(args.stop_hotkey)
        print(
            f"Ready for {mode}. Get the game positioned, then press "
            f"{start_display} to start"
            + ("" if args.dry_run else f" ({stop_display} force-stops at any time)")
            + "."
        )
        hotkeys.wait_for_start()
        _run(args, goal, common_kwargs, should_stop=hotkeys.stop_requested)


def _run(args, goal, common_kwargs, should_stop) -> None:
    if args.dry_run:
        log = AttemptLogger(goal=goal)
        try:
            decision = state_machine.evaluate_current_screen(
                goal, log=log, should_stop=should_stop, **common_kwargs,
            )
        except state_machine.UnreadableRollError as e:
            print(f"UNREADABLE: {e}", file=sys.stderr)
            print(f"Full step-by-step trace: {log._debug_path}", file=sys.stderr)
            sys.exit(2)
        sys.exit(0 if decision.accepted else 1)

    try:
        result = state_machine.run(
            goal, max_attempts=args.max_attempts, should_stop=should_stop, **common_kwargs,
        )
    except state_machine.UnreadableRollError as e:
        print(f"UNREADABLE, stopping: {e}", file=sys.stderr)
        print(f"Full step-by-step trace: logs/{goal.name}-*.debug.log (most recent)", file=sys.stderr)
        sys.exit(2)

    if result.accepted:
        print(f"\nAccepted after {result.attempts} attempt(s).")
        sys.exit(0)
    elif result.stopped:
        sys.exit(130)  # conventional exit code for a user-initiated interrupt
    else:
        print(f"\nGave up after {result.attempts} attempts without a match.")
        sys.exit(1)


if __name__ == "__main__":
    main()
