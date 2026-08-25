"""Shared terminal UI building blocks -- the arrow-key/number-key
full-screen menu widget (originally built for main.py's top-level
interactive menu) plus consistently-colored text prompts/output, used by
both main.py and goal_wizard.py so every choice-menu in the app looks and
behaves the same way instead of each file growing its own variant.

Two genuinely different reliability tiers here, by design:

- The full-screen widget (show_arrow_menu/pick/pick_index/ask_yes_no)
  needs prompt_toolkit's Application, which needs a real attached
  console -- confirmed live (see CHANGELOG): it raises
  NoConsoleScreenBufferError constructing the Application itself
  whenever stdout is redirected/piped, even from a real cmd.exe/
  PowerShell session. Every one of these functions degrades to a plain
  numbered input() picker automatically when that happens.
- Everything else (ask_text/ask_int/print_header/color primitives) uses
  raw ANSI escape codes directly, never prompt_toolkit's output layer --
  confirmed live that prompt_toolkit.print_formatted_text has the exact
  same console-detection failure as Application, which would defeat the
  purpose of a "this always works" text prompt. Raw ANSI has no such
  detection step: modern Windows terminals render it, anything that
  doesn't just passes the bytes through unharmed.
"""
from __future__ import annotations

from prompt_toolkit import PromptSession
from prompt_toolkit.application import Application
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import Frame

# Generated once via `pyfiglet.figlet_format("QURIO", font="small")` and
# hardcoded here -- not worth a runtime dependency for a static string.
BANNER = [
    r"  ___  _   _ ___ ___ ___  ",
    r" / _ \| | | | _ \_ _/ _ \ ",
    r"| (_) | |_| |   /| | (_) |",
    r" \__\_\\___/|_|_\___\___/ ",
]

STYLE = Style.from_dict({
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

Fragment = tuple[str, str]
Item = tuple[str, str]  # (id, label)


def _build_arrow_menu_app(
    title: str,
    items: list[Item],
    body: list[Fragment] | None,
    show_banner: bool,
    default: str | None,
    on_ctrl_c: str,
) -> Application[str | None]:
    ids = [item_id for item_id, _ in items]
    selected = [ids.index(default) if default in ids else 0]

    def get_text():
        fragments: list[Fragment] = []
        if show_banner:
            for line in BANNER:
                fragments.append(("class:banner", line + "\n"))
            fragments.append(("class:tagline", "Augmentation Automation\n\n"))
        if body:
            fragments.extend(body)
            fragments.append(("", "\n"))
        for i, (_, label) in enumerate(items):
            number = f"{i + 1}. " if i < 9 else "   "
            if i == selected[0]:
                fragments.append(("class:selected", f" > {number}{label} \n"))
            else:
                fragments.append(("class:item-number", f"   {number}"))
                fragments.append(("class:item", f"{label}\n"))
        fragments.append(("class:help", "\nUp/Down or a number to move, Enter to run, Esc to cancel.\n"))
        return fragments

    kb = KeyBindings()

    @kb.add("up")
    def _move_up(event) -> None:
        selected[0] = (selected[0] - 1) % len(items)

    @kb.add("down")
    def _move_down(event) -> None:
        selected[0] = (selected[0] + 1) % len(items)

    @kb.add("enter")
    def _confirm(event) -> None:
        event.app.exit(result=items[selected[0]][0])

    @kb.add("escape")
    def _cancel(event) -> None:
        event.app.exit(result=None)

    if on_ctrl_c == "raise":
        @kb.add("c-c")
        def _abort(event) -> None:
            event.app.exit(exception=KeyboardInterrupt())
    else:
        @kb.add("c-c")
        def _cancel_ctrl_c(event) -> None:
            event.app.exit(result=None)

    for i in range(min(len(items), 9)):
        @kb.add(str(i + 1))
        def _jump(event, i=i) -> None:
            event.app.exit(result=items[i][0])

    frame = Frame(Window(FormattedTextControl(get_text)), title=title)
    return Application(
        layout=Layout(frame),
        key_bindings=kb,
        style=STYLE,
        full_screen=True,
        mouse_support=True,
    )


def show_arrow_menu(
    title: str,
    items: list[Item],
    body: list[Fragment] | None = None,
    *,
    show_banner: bool = False,
    default: str | None = None,
    on_ctrl_c: str = "cancel",
) -> tuple[bool, str | None]:
    """Low-level full-screen arrow-key/number-key menu. Returns
    (available, selected_id). available=False means the console couldn't
    show it at all (see module docstring) -- the caller decides how to
    degrade; `pick`/`pick_index`/`ask_yes_no` below do that automatically.
    selected_id is None when available=True but the user cancelled
    (Escape, or Ctrl-C when on_ctrl_c="cancel").

    on_ctrl_c="raise" makes Ctrl-C raise KeyboardInterrupt instead (via
    Application.exit(exception=...), the documented mechanism for a
    full-screen app to propagate an exception after restoring the
    terminal) -- for a menu nested inside a larger flow (the wizard/
    editor) where Ctrl-C needs to abort everything, not just this one
    menu, exactly like it already does today via plain input().
    """
    try:
        app = _build_arrow_menu_app(title, items, body, show_banner, default, on_ctrl_c)
        result = app.run()
    except KeyboardInterrupt:
        raise
    except Exception:
        return False, None
    return True, result


def _fallback_pick(title: str, items: list[Item], body: list[Fragment] | None, default: str | None) -> str | None:
    """Plain numbered print()+input() picker -- what every menu in this
    app looked like before the arrow-key widget existed, used here only
    when the widget itself can't be shown. Ctrl-C already raises
    KeyboardInterrupt on its own via input(), matching on_ctrl_c="raise"
    without any special handling needed. `body`'s style-class half is
    dropped (plain print can't render it) -- just its text, so a goal
    summary or status panel still shows up here instead of being lost.
    """
    print(f"\n{title}")
    if body:
        print("".join(text for _, text in body), end="")
    for i, (_, label) in enumerate(items, 1):
        print(f"  {i}. {label}")
    suffix = f" (blank for {default}): " if default else " (blank to cancel): "
    raw = input("Pick a number" + suffix).strip()
    if not raw:
        return default
    if raw.isdigit():
        idx = int(raw)
        if 1 <= idx <= len(items):
            return items[idx - 1][0]
        print(f"{idx} isn't one of the listed options.")
        return None
    print(f"{raw!r} isn't a number.")
    return None


def pick(
    title: str,
    items: list[Item],
    body: list[Fragment] | None = None,
    *,
    default: str | None = None,
    on_ctrl_c: str = "raise",
) -> str | None:
    """High-level picker: tries the arrow-key menu, degrades to a plain
    numbered picker automatically if the console can't show it -- the
    caller never sees the "unavailable" case, unlike show_arrow_menu.
    """
    available, selected = show_arrow_menu(title, items, body, default=default, on_ctrl_c=on_ctrl_c)
    if available:
        return selected
    return _fallback_pick(title, items, body, default)


def pick_index(
    title: str,
    entries: list[str],
    body: list[Fragment] | None = None,
    *,
    default: int | None = None,
    on_ctrl_c: str = "raise",
) -> int | None:
    """pick(), specialized for "choose one of these existing things" --
    returns the chosen index into `entries`, or None if cancelled.
    """
    items = [(str(i), label) for i, label in enumerate(entries)]
    default_id = str(default) if default is not None else None
    result = pick(title, items, body, default=default_id, on_ctrl_c=on_ctrl_c)
    return int(result) if result is not None else None


def ask_yes_no(question: str, default: bool = False, *, on_ctrl_c: str = "raise") -> bool:
    items: list[Item] = [("yes", "Yes"), ("no", "No")]
    result = pick(question, items, default="yes" if default else "no", on_ctrl_c=on_ctrl_c)
    return result == "yes"


# --- Always-available styled text I/O -- raw ANSI, no prompt_toolkit
# output layer involved (see module docstring for why). ---

_RESET = "\x1b[0m"
_CYAN = "\x1b[36m"
_CYAN_BOLD = "\x1b[1;36m"
_DIM = "\x1b[2m"
_GREEN = "\x1b[32m"
_YELLOW = "\x1b[33m"
_RED = "\x1b[31m"


def accent(text: str) -> str:
    return f"{_CYAN}{text}{_RESET}"


def dim(text: str) -> str:
    return f"{_DIM}{text}{_RESET}"


def good(text: str) -> str:
    return f"{_GREEN}{text}{_RESET}"


def warn(text: str) -> str:
    return f"{_YELLOW}{text}{_RESET}"


def bad(text: str) -> str:
    return f"{_RED}{text}{_RESET}"


def print_header(text: str) -> None:
    print(f"\n{_CYAN_BOLD}{text}{_RESET}")


def print_success(text: str) -> None:
    print(good(text))


def print_error(text: str) -> None:
    print(bad(text))


def ask_text(prompt: str) -> str:
    return input(accent(prompt)).strip()


def ask_text_with_completion(prompt: str, words: list[str]) -> str:
    """Like ask_text, but with tab-completion over `words` (e.g. skill
    names while building a goal) when a real console is available --
    falls back to plain ask_text automatically otherwise, same
    console-detection failure mode as the arrow-key widgets (see module
    docstring): constructing a real PromptSession needs a console too.
    """
    try:
        session = PromptSession(completer=WordCompleter(words, ignore_case=True))
        return session.prompt(ANSI(accent(prompt))).strip()
    except Exception:
        return ask_text(prompt)


def ask_int(prompt: str, default: int, *, min_value: int | None = None) -> int:
    raw = ask_text(f"{prompt} (default {default}): ")
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        print_error(f"  not a number, using default ({default})")
        return default
    if min_value is not None and value < min_value:
        print_error(f"  must be at least {min_value}, using default ({default})")
        return default
    return value
