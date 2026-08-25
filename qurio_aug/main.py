"""CLI entry point.

Usage:
  # Don't know where to start? Run with no arguments for an interactive
  # menu -- this is also what happens if you double-click the compiled
  # exe, so it doesn't just flash open and close.
  python -m qurio_aug.main

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
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from prompt_toolkit import PromptSession
from prompt_toolkit.application import Application
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import Frame

from qurio_aug import calibrate, capture, ocr, state_machine
from qurio_aug.goal_config import GoalValidationError, load_goal
from qurio_aug.goal_wizard import run_editor, run_wizard
from qurio_aug.hotkeys import DEFAULT_START_HOTKEY, DEFAULT_STOP_HOTKEY, HotkeyController
from qurio_aug.input import POST_PRESS_DELAY, PRESS_HOLD
from qurio_aug.logger import AttemptLogger
from qurio_aug.support_bundle import build_support_bundle
from qurio_aug.tesseract_setup import configure_tesseract, selfcheck

# Where the interactive menu (and --wizard) look for/write goal YAMLs --
# configs/goals/ ships the bundled examples, goals/ is where the wizard's
# own output lands (see goal_wizard.py for why those are kept separate).
_GOAL_SEARCH_DIRS = (Path("configs/goals"), Path("goals"))

# Fallback for --no-hotkeys. Both --dry-run (which may send Q/E to page
# through a multi-page roll) and the full run send real keystrokes via
# pynput, which only reach the game if it's the focused window -- not the
# terminal this was launched from. This grace period gives you time to
# Alt/Cmd-Tab over after hitting Enter.
DEFAULT_START_DELAY = 3.0


_MODIFIER_DISPLAY_NAMES = {
    "alt": "Alt" if sys.platform == "win32" else "Option",
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


def _tesseract_status_lines() -> tuple[list[str], bool]:
    """Returns (status lines, whether tesseract itself failed) -- shared
    by --selfcheck and the REPL's `status` command so there's exactly
    one place that knows how to ask.
    """
    try:
        version = selfcheck()
    except RuntimeError as e:
        return [f"tesseract: FAILED -- {e}"], True
    lines = [f"tesseract: OK (version {version})"]
    if sys.platform == "win32":
        # In-process OCR accelerator (~3.76x measured, see
        # docs/ocr-performance-research.md #1b) -- optional, so report
        # its status rather than fail selfcheck over it: a beta tester
        # seeing "inactive" here explains a slower-than-expected run
        # without it being an error to chase.
        if ocr.tesserocr is None:
            lines.append(f"tesserocr accelerator: inactive (not installed) [{ocr._tesserocr_import_error}]")
        elif ocr._get_tesserocr_api() is None:
            lines.append("tesserocr accelerator: inactive (tessdata not found)")
        else:
            lines.append("tesserocr accelerator: active")
    return lines, False


def _run_selfcheck() -> None:
    """Prints enough to diagnose "it doesn't work" from a single command's
    output -- the thing to ask a beta tester to run and paste, especially
    on a compiled build where there's no source to poke through.
    """
    print(f"platform: {sys.platform}")
    print(f"capture backend: {capture._backend.__name__}")
    lines, failed = _tesseract_status_lines()
    for line in lines:
        print(line, file=sys.stderr if failed else sys.stdout)
    if failed:
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


def _run_package_failure() -> None:
    bundle = build_support_bundle()
    if bundle is None:
        print("nothing to package -- no debug log or saved failure screenshots "
              "found in logs/ yet. Run a dry-run or a real attempt first.")
        return
    print(f"wrote {bundle.resolve()}")
    print("attach this to a bug report on the project's GitHub Issues page.")


def _select_goal_path(default: str | None = None) -> str | None:
    """Lists goal YAMLs from _GOAL_SEARCH_DIRS and lets the user pick one
    by number, or type/paste a path themselves -- so the interactive menu
    doesn't require already knowing (or typing out) a file path.

    `default` (the REPL session's last-used goal, if any) is returned on
    blank input instead of None -- lets repeat dry-run/farm invocations
    just hit Enter to reuse the same goal rather than re-picking from the
    list every time. Omitting it preserves the original "blank cancels"
    behavior exactly (used by `edit`, which has no session goal to reuse).
    """
    candidates = [p for d in _GOAL_SEARCH_DIRS if d.is_dir() for p in sorted(d.glob("*.yaml"))]
    if candidates:
        print("\nAvailable goal configs:")
        for i, p in enumerate(candidates, 1):
            print(f"  {i}. {p}")
    else:
        print("\nNo goal configs found yet in configs/goals/ or goals/ -- "
              "build one first with the wizard (option 1).")
    prompt = f"\nPick a number, or paste a path to a goal YAML (blank for {default}): " if default else \
        "\nPick a number, or paste a path to a goal YAML (blank to cancel): "
    raw = input(prompt).strip()
    if not raw:
        return default
    if raw.isdigit():
        idx = int(raw)
        if 1 <= idx <= len(candidates):
            return str(candidates[idx - 1])
        print(f"{idx} isn't one of the listed options.")
        return None
    return raw  # typed/pasted path, used as-is


def _prompt_max_attempts(default: int) -> int:
    """Only meaningful for a real farming run (choice 4) -- dry-run
    (choice 3) ignores max_attempts entirely (see _execute), so it isn't
    asked there.
    """
    raw = input(f"\nMax attempts before giving up (blank for default of {default}): ").strip()
    if not raw:
        return default
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    print(f"{raw!r} isn't a positive number -- using default of {default}.")
    return default


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


@dataclass
class _SessionState:
    """Holds what the REPL loop needs to remember between commands --
    region_config is loaded once up front (cheap: just a parsed YAML),
    window_hint is a per-session override set either explicitly (`window
    <hint>`) or recovered from a WindowNotFoundError/AmbiguousWindowError
    (see _recover_window_error), and last_goal_path lets repeat
    dry-run/farm invocations reuse the same goal without re-picking it.
    """
    region_config: ocr.RegionConfig
    window_hint: str | None = None
    last_goal_path: str | None = None


def _resolve_window_hint(session: _SessionState) -> str:
    return session.window_hint or session.region_config.window_title_hint


def _pick_window_interactively(matches: list[capture.WindowInfo]) -> capture.WindowInfo | None:
    """Same shape as _select_goal_path (numbered list, blank cancels) --
    lets _recover_window_error offer a concrete choice instead of just
    telling the user to go run --list-windows themselves.
    """
    print("\nVisible windows:")
    for i, w in enumerate(matches, 1):
        print(f"  {i}. owner={w.owner_name!r} title={w.title!r}")
    raw = input("\nPick a number (blank to cancel): ").strip()
    if not raw:
        return None
    if raw.isdigit():
        idx = int(raw)
        if 1 <= idx <= len(matches):
            return matches[idx - 1]
        print(f"{idx} isn't one of the listed options.")
        return None
    print(f"{raw!r} isn't a number.")
    return None


def _recover_window_error(err: Exception, session: _SessionState) -> bool:
    """Handles the three window-related exceptions calibrate/dry-run/farm
    can raise by offering an interactive pick instead of crashing the
    whole REPL with a traceback. Returns True if the caller should retry
    the command (a window was picked), False to give up quietly.
    """
    print(f"\n{err}")
    if isinstance(err, capture.AmbiguousWindowError):
        candidates = err.matches  # already computed by find_game_window -- no need to re-query
    elif isinstance(err, capture.WindowNotFoundError):
        candidates = capture.find_windows("")
        if not candidates:
            print("no visible windows at all -- is the game running?")
            return False
    else:  # ScreenCapturePermissionError -- an OS permission grant, nothing to pick here
        return False

    picked = _pick_window_interactively(candidates)
    if picked is None:
        return False
    session.window_hint = picked.owner_name or picked.title
    print(f"using {session.window_hint!r} for this session -- retrying...")
    return True


def _run_with_window_recovery(action: Callable[[], None], session: _SessionState) -> None:
    while True:
        try:
            action()
            return
        except (capture.WindowNotFoundError, capture.AmbiguousWindowError, capture.ScreenCapturePermissionError) as e:
            if not _recover_window_error(e, session):
                return


def _status_summary(session: _SessionState) -> list[str]:
    """Human-readable tesseract/window/goal status lines -- shared by the
    plain-text `status` command (_print_status) and the arrow-key menu's
    colored status panel (_status_fragments), so there's one place that
    computes it.
    """
    lines, _ = _tesseract_status_lines()
    hint = _resolve_window_hint(session)
    matches = capture.find_windows(hint)
    if len(matches) == 1:
        w = matches[0]
        lines.append(f"window: found (owner={w.owner_name!r} title={w.title!r})")
    elif len(matches) > 1:
        lines.append(f"window: {len(matches)} windows match {hint!r} -- ambiguous, will prompt when needed")
    else:
        lines.append(f"window: not found (looking for {hint!r} -- use 'window <hint>' to override)")
    lines.append(f"goal: {session.last_goal_path or 'none selected yet'}")
    return lines


def _classify_status_line(line: str) -> str:
    """"good"/"warn"/"bad"/"neutral" for coloring the arrow-key menu's
    status panel -- based on the line's own text, since _status_summary
    already produces human-readable status strings and re-deriving
    structured state here would just duplicate what they already say.
    """
    if line.startswith("goal:"):
        return "neutral"
    lowered = line.lower()
    if lowered.startswith("tesseract:") and "failed" in lowered:
        return "bad"  # a real blocking problem, not just "not set up yet"
    if "inactive" in lowered or "ambiguous" in lowered or "not found" in lowered:
        return "warn"  # expected/common (e.g. game not open yet), not an error
    return "good"


def _status_fragments(session: _SessionState) -> list[tuple[str, str]]:
    return [(f"class:status-{_classify_status_line(line)}", line + "\n") for line in _status_summary(session)]


def _print_status(session: _SessionState) -> None:
    for line in _status_summary(session):
        print(line)


_COMMANDS: list[tuple[tuple[str, ...], str]] = [
    (("1", "wizard", "goal"), "Build a new goal config (wizard)"),
    (("2", "edit"), "Edit an existing goal"),
    (("3", "calibrate"), "Calibrate against your window"),
    (("4", "dry-run"), "Test a goal against the current screen (safe, won't accept/reject/reroll)"),
    (("5", "farm"), "Start farming with a goal"),
    (("6", "selfcheck"), "Run diagnostics"),
    (("7", "windows"), "List visible windows"),
    (("8", "package-failure", "bug-report"), "Package up my last failure (for a bug report)"),
    (("window",), "Show or set the window title/owner hint for this session, e.g. 'window Monster Hunter'"),
    (("status",), "Show tesseract/window/goal status"),
    (("help", "?"), "Show this command list"),
    (("0", "exit", "quit", "q"), "Exit"),
]


def _build_completer() -> WordCompleter:
    words = [name for names, _ in _COMMANDS for name in names if not name.isdigit()]
    return WordCompleter(words, ignore_case=True)


def _make_command_reader(completer: WordCompleter, history_path: Path) -> Callable[[str], str]:
    """Returns a read(label) -> str function for the REPL's main loop.
    Prefers prompt_toolkit (tab-completion, persistent history, a
    colored prompt), but falls back to plain input() if prompt_toolkit
    can't get a real console -- confirmed live: its Windows output
    backend needs a genuine attached console handle and raises
    constructing PromptSession itself (not lazily, so this try/except
    catches it up front) whenever stdout is redirected/piped, even from
    a real cmd.exe/PowerShell session. Falling back keeps the REPL
    itself always usable instead of crashing over a cosmetic feature.
    """
    try:
        session = PromptSession(completer=completer, history=FileHistory(str(history_path)))
    except Exception:
        print("(tab-completion/history unavailable in this console -- falling back to plain input)")
        return lambda label: input(f"\n{label}")
    return lambda label: session.prompt(HTML(f"\n<ansicyan>{label}</ansicyan>"))


def _print_help() -> None:
    print("\nCommands:")
    for names, desc in _COMMANDS:
        primary = ", ".join(n for n in names if not n.isdigit()) or names[0]
        print(f"  {primary:<28s} {desc}")


# Command id -> menu label, in display order. A dedicated list rather than
# derived from _COMMANDS (which carries numbered aliases and a couple of
# entries -- "status", "help" -- that don't make sense as their own row
# once the menu always shows status and the whole command list up front)
# since the two are rendered in genuinely different contexts.
_MENU_ITEMS: list[tuple[str, str]] = [
    ("wizard", "Build a new goal config (wizard)"),
    ("edit", "Edit an existing goal"),
    ("calibrate", "Calibrate against your window"),
    ("dry-run", "Test a goal against the current screen (safe, won't accept/reject/reroll)"),
    ("farm", "Start farming with a goal"),
    ("selfcheck", "Run diagnostics"),
    ("windows", "List visible windows"),
    ("package-failure", "Package up my last failure (for a bug report)"),
    ("window", "Set/override the window title/owner hint for this session"),
    ("exit", "Exit"),
]

# Generated once via `pyfiglet.figlet_format("QURIO", font="small")` and
# hardcoded here -- not worth a runtime dependency for a static string.
_BANNER = [
    r"  ___  _   _ ___ ___ ___  ",
    r" / _ \| | | | _ \_ _/ _ \ ",
    r"| (_) | |_| |   /| | (_) |",
    r" \__\_\\___/|_|_\___\___/ ",
]

_MENU_STYLE = Style.from_dict({
    "frame.border": "#00d7d7",
    "frame.label": "bold #00d7d7",
    "banner": "bold #00d7d7",
    "tagline": "#5f8787 italic",
    "status-good": "#00d787",
    "status-warn": "#ffaf00",
    "status-bad": "#ff5f5f",
    "status-neutral": "#8a8a8a",
    "item": "#d0d0d0",
    "item-number": "#5f8787",
    "selected": "bg:#00d7d7 #000000 bold",
    "help": "#5f5f5f italic",
})


def _build_arrow_menu_app(session: _SessionState) -> Application[str | None]:
    """A full-screen arrow-key/number-key menu (Up/Down or a digit to
    move, Enter to run immediately -- no separate "Ok" button to tab to,
    unlike prompt_toolkit's own radiolist_dialog) showing live
    tesseract/window/goal status above the command list, so both are
    visible without needing to type 'status'/'help' first. Rebuilt fresh
    every loop iteration (see _interactive_menu) so status reflects
    whatever changed since the last command.
    """
    selected = [0]
    status = _status_fragments(session)

    def get_text():
        fragments = []
        for line in _BANNER:
            fragments.append(("class:banner", line + "\n"))
        fragments.append(("class:tagline", "Augmentation Automation\n\n"))
        fragments.extend(status)
        fragments.append(("", "\n"))
        for i, (_, label) in enumerate(_MENU_ITEMS):
            number = f"{i + 1}. " if i < 9 else "   "
            if i == selected[0]:
                fragments.append(("class:selected", f" > {number}{label} \n"))
            else:
                fragments.append(("class:item-number", f"   {number}"))
                fragments.append(("class:item", f"{label}\n"))
        fragments.append(("class:help", "\nUp/Down or a number to move, Enter to run, Esc/Ctrl-C to exit.\n"))
        return fragments

    kb = KeyBindings()

    @kb.add("up")
    def _move_up(event) -> None:
        selected[0] = (selected[0] - 1) % len(_MENU_ITEMS)

    @kb.add("down")
    def _move_down(event) -> None:
        selected[0] = (selected[0] + 1) % len(_MENU_ITEMS)

    @kb.add("enter")
    def _confirm(event) -> None:
        event.app.exit(result=_MENU_ITEMS[selected[0]][0])

    @kb.add("c-c")
    @kb.add("escape")
    def _cancel(event) -> None:
        event.app.exit(result=None)

    for i in range(min(len(_MENU_ITEMS), 9)):
        @kb.add(str(i + 1))
        def _jump(event, i=i) -> None:
            event.app.exit(result=_MENU_ITEMS[i][0])

    body = Frame(Window(FormattedTextControl(get_text)), title="Qurio Augmentation Automation")
    return Application(
        layout=Layout(body),
        key_bindings=kb,
        style=_MENU_STYLE,
        full_screen=True,
        mouse_support=True,
    )


def _show_arrow_menu(session: _SessionState) -> tuple[bool, str | None]:
    """Returns (available, selected_id). available=False means the
    full-screen UI couldn't even be shown (no real console -- confirmed
    live: this fails the same way _make_command_reader's PromptSession
    does when stdout is redirected/piped), so the caller should fall
    back to the typed-command REPL for the rest of the session.
    selected_id is None when available=True but the user cancelled
    (Esc/Ctrl-C), which exits the whole program.
    """
    try:
        app = _build_arrow_menu_app(session)
        result = app.run()
    except Exception:
        return False, None
    return True, result


def _interactive_menu() -> None:
    """Zero-arguments fallback -- this is what runs if you double-click
    the compiled exe (which passes no arguments) instead of it just
    flashing a window open and closed. Everything here is also reachable
    via CLI flags for scripting/tuning; this just picks sensible
    defaults and walks through the same options with prompts instead of
    needing to know the flags up front.

    Primary interface is a full-screen arrow-key/number-key menu
    (_show_arrow_menu) showing live tesseract/window/goal status above
    the always-visible command list -- no need to type 'help'/'status'
    first. Falls back to a named-command REPL (prompt_toolkit:
    tab-completion + persistent history; old numbers still work as
    aliases) if the arrow-key menu can't get a real console either
    (confirmed live: same failure mode as _make_command_reader's
    PromptSession) -- checked once at startup and once more per loop in
    case the very first attempt fails, then stays in whichever mode
    worked. calibrate/dry-run/farm all route through
    _run_with_window_recovery so a WindowNotFoundError/AmbiguousWindowError
    (previously an uncaught crash -- see the CHANGELOG) offers an
    interactive pick instead.
    """
    session = _SessionState(region_config=ocr.load_region_config())
    Path("logs").mkdir(exist_ok=True)
    read_command = _make_command_reader(_build_completer(), Path("logs") / ".repl_history")
    use_arrow_menu = True

    print("Qurio Augmentation Automation")
    if not use_arrow_menu:
        print("Type 'help' to see commands (the old numbered options still work too).\n")
        _print_status(session)

    while True:
        if use_arrow_menu:
            available, selected = _show_arrow_menu(session)
            if not available:
                use_arrow_menu = False
                print("(arrow-key menu unavailable in this console -- "
                      "falling back to typed commands; 'help' lists them)")
                _print_status(session)
                continue
            if selected is None:
                return  # Esc/Ctrl-C
            cmd, rest = selected, ""
            if cmd == "window":
                rest = input("Window hint (blank to cancel): ").strip()
                if not rest:
                    continue
        else:
            label = f"qurio-aug [{session.last_goal_path}]> " if session.last_goal_path else "qurio-aug> "
            try:
                raw = read_command(label)
            except (EOFError, KeyboardInterrupt):
                print()
                return
            # Confirmed live: PowerShell prepends a BOM (U+FEFF) to the
            # very first line of piped/redirected stdin -- harmless to
            # strip unconditionally since it's never a legitimate
            # command character.
            raw = raw.strip().lstrip("﻿")
            if not raw:
                continue
            cmd, _, rest = raw.partition(" ")
            cmd, rest = cmd.lower(), rest.strip()

        if not _dispatch_command(cmd, rest, session):
            return
        if use_arrow_menu:
            # Otherwise the next redraw's full-screen alternate buffer
            # covers up whatever this command just printed -- a farm
            # run's live progress readout, a force-stop/give-up result, a
            # goal validation error -- before there's been any chance to
            # read it. Not needed in the typed-REPL fallback, which never
            # takes over the screen in the first place.
            input("\nPress Enter to return to the menu...")


def _dispatch_command(cmd: str, rest: str, session: _SessionState) -> bool:
    """Runs one command. Returns False if the REPL should exit (the user
    picked exit/quit), True otherwise -- including every early-cancel
    path (goal picking cancelled, a goal file failed to load), so
    _interactive_menu's caller can uniformly pause afterward regardless
    of which branch actually ran.
    """
    if cmd in ("0", "exit", "quit", "q"):
        return False
    elif cmd in ("1", "wizard", "goal"):
        run_wizard()
    elif cmd in ("2", "edit"):
        goal_path = _select_goal_path()
        if goal_path is None:
            return True
        try:
            goal = load_goal(goal_path)
        except GoalValidationError as e:
            print(f"goal config problem in {goal_path}:\n{e}", file=sys.stderr)
            return True
        except OSError as e:
            print(f"couldn't read {goal_path}: {e}", file=sys.stderr)
            return True
        run_editor(goal, Path(goal_path))
    elif cmd in ("3", "calibrate"):
        _run_with_window_recovery(lambda: calibrate.main(_resolve_window_hint(session)), session)
    elif cmd in ("4", "dry-run", "5", "farm"):
        is_farm = cmd in ("5", "farm")
        goal_path = _select_goal_path(default=session.last_goal_path)
        if goal_path is None:
            return True
        try:
            goal = load_goal(goal_path)
        except GoalValidationError as e:
            print(f"goal config problem in {goal_path}:\n{e}", file=sys.stderr)
            return True
        except OSError as e:
            print(f"couldn't read {goal_path}: {e}", file=sys.stderr)
            return True
        session.last_goal_path = goal_path
        max_attempts = state_machine.MAX_ATTEMPTS_DEFAULT
        if is_farm:
            max_attempts = _prompt_max_attempts(state_machine.MAX_ATTEMPTS_DEFAULT)
        common_kwargs = dict(
            window_title_hint=_resolve_window_hint(session),
            region_config=session.region_config,
            press_hold=PRESS_HOLD,
            post_press_delay=POST_PRESS_DELAY,
            settle_delay=state_machine.RESULT_SETTLE_DELAY,
        )
        _run_with_window_recovery(
            lambda: _run_with_hotkeys(
                goal, dry_run=not is_farm, max_attempts=max_attempts,
                common_kwargs=common_kwargs,
                start_hotkey=DEFAULT_START_HOTKEY, stop_hotkey=DEFAULT_STOP_HOTKEY,
            ),
            session,
        )
    elif cmd in ("6", "selfcheck"):
        _run_selfcheck()
    elif cmd in ("7", "windows"):
        _list_windows()
    elif cmd in ("8", "package-failure", "bug-report"):
        _run_package_failure()
    elif cmd == "window":
        if not rest:
            print(f"current window hint: {_resolve_window_hint(session)!r}")
        else:
            session.window_hint = rest
            print(f"window hint set to {rest!r} for this session")
    elif cmd == "status":
        _print_status(session)
    elif cmd in ("help", "?"):
        _print_help()
    else:
        shown = f"{cmd} {rest}".strip() if rest else cmd
        print(f"{shown!r} isn't a recognized command -- type 'help' to see the list.")
    return True


def main() -> None:
    if sys.platform == "win32":
        # The console's legacy codepage (cp1252 etc.) can't encode a lot of
        # real-world window titles (styled Discord servers, VS Code's "..."
        # truncation) -- crashed --list-windows outright on first real
        # Windows testing. Printing "?" for what the console couldn't show
        # anyway beats crashing the one diagnostic tool meant to help
        # someone find the right --window value.
        sys.stdout.reconfigure(errors="replace")
        sys.stderr.reconfigure(errors="replace")
        # Confirmed live against the compiled exe specifically (not the
        # source venv): redirected/piped stdin defaults to the legacy
        # console codepage there, which doesn't understand UTF-8 -- a
        # leading BOM (PowerShell prepends one piping into a native exe)
        # came through as 3 mis-decoded Latin-1-ish characters instead of
        # one U+FEFF, which the REPL's own BOM-strip couldn't catch since
        # it wasn't looking for that. Forcing UTF-8 here fixes decoding at
        # the source; real interactive keyboard input on Windows is read
        # via a separate console API path CPython special-cases, so this
        # only affects the redirected/piped case where it was broken.
        sys.stdin.reconfigure(encoding="utf-8")
    if len(sys.argv) == 1:
        configure_tesseract()
        try:
            _interactive_menu()
        except KeyboardInterrupt:
            print()
        return

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
        "--package-failure", action="store_true",
        help="bundle the most recent debug log (+ matching .log/.jsonl) and "
        "the most recent saved failure screenshots from logs/ into one zip, "
        "and exit -- attach the result to a bug report instead of hunting "
        "through logs/ by hand",
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

    if args.package_failure:
        _run_package_failure()
        return

    if args.wizard:
        run_wizard()
        return

    if not args.goal:
        parser.error(
            "one of --goal, --wizard, --selfcheck, --list-windows, or "
            "--package-failure is required"
        )

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

    try:
        if args.no_hotkeys:
            _countdown(args.start_delay)
            sys.exit(_execute(goal, args.dry_run, args.max_attempts, lambda: False, **common_kwargs))

        sys.exit(_run_with_hotkeys(
            goal, args.dry_run, args.max_attempts, common_kwargs, args.start_hotkey, args.stop_hotkey,
        ))
    except (capture.WindowNotFoundError, capture.AmbiguousWindowError, capture.ScreenCapturePermissionError) as e:
        # Previously an uncaught traceback -- a script/CLI invocation isn't
        # expecting a prompt, so this stays fail-fast (unlike the REPL's
        # interactive _run_with_window_recovery), just with a clean message.
        print(f"{e}", file=sys.stderr)
        print("-- pass --window to override, or run --list-windows to see what's visible", file=sys.stderr)
        sys.exit(1)


def _execute(goal, dry_run: bool, max_attempts: int, should_stop, **common_kwargs) -> int:
    """Runs a dry-run evaluation or the full autonomous loop and returns
    a process-exit-code-like int (0=accepted, 1=rejected/gave up,
    2=unreadable error, 130=force-stopped) -- doesn't call sys.exit
    itself, so both main()'s argparse path (which does, for scripting)
    and the interactive menu (which just prints and loops back to the
    menu afterward) can share this.
    """
    if dry_run:
        log = AttemptLogger(goal=goal)
        try:
            decision = state_machine.evaluate_current_screen(
                goal, log=log, should_stop=should_stop, **common_kwargs,
            )
        except state_machine.UnreadableRollError as e:
            print(f"UNREADABLE: {e}", file=sys.stderr)
            print(f"Full step-by-step trace: {log._debug_path}", file=sys.stderr)
            return 2
        return 0 if decision.accepted else 1

    try:
        result = state_machine.run(
            goal, max_attempts=max_attempts, should_stop=should_stop, **common_kwargs,
        )
    except state_machine.UnreadableRollError as e:
        print(f"UNREADABLE, stopping: {e}", file=sys.stderr)
        print(f"Full step-by-step trace: logs/{goal.name}-*.debug.log (most recent)", file=sys.stderr)
        return 2

    if result.accepted:
        print(f"\nAccepted after {result.attempts} attempt(s).")
        return 0
    elif result.stopped:
        print(f"\nStopped after {result.attempts} attempt(s).")
        return 130  # conventional exit code for a user-initiated interrupt
    else:
        print(f"\nGave up after {result.attempts} attempts without a match.")
        return 1


def _run_with_hotkeys(
    goal, dry_run: bool, max_attempts: int, common_kwargs: dict,
    start_hotkey: str, stop_hotkey: str,
) -> int:
    """Waits for the start hotkey, then runs -- shared by main()'s
    default (hotkey-driven) CLI path and the interactive menu, so both
    get the same "position the game, then press to start" UX.
    """
    with HotkeyController(start_hotkey, stop_hotkey) as hotkeys:
        mode = "dry-run evaluation" if dry_run else "autonomous loop"
        start_display = _format_hotkey(start_hotkey)
        stop_display = _format_hotkey(stop_hotkey)
        print(
            f"Ready for {mode}. Get the game positioned, then press "
            f"{start_display} to start"
            + ("" if dry_run else f" ({stop_display} force-stops at any time)")
            + "."
        )
        hotkeys.wait_for_start()
        return _execute(goal, dry_run, max_attempts, hotkeys.stop_requested, **common_kwargs)


if __name__ == "__main__":
    main()
