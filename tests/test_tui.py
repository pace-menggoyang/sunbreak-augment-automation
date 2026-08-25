"""Offline tests for tui.py's arrow-key menu widget and always-available
styled text helpers.

show_arrow_menu/pick/pick_index/ask_yes_no's automatic degrade-to-plain-
input path is exercised by forcing prompt_toolkit's Application
construction to fail -- the same real failure mode confirmed live
against a real compiled exe (redirected/piped stdout, even from a real
cmd.exe/PowerShell session -- see CHANGELOG), not a hypothetical one.
ask_text/ask_int never touch prompt_toolkit's output layer at all (see
tui.py's module docstring for why), so they're tested directly against
plain input().
"""
import builtins
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qurio_aug import tui


def _force_fallback(monkeypatch):
    def boom(**kw):
        raise Exception("simulated: no console")

    monkeypatch.setattr(tui, "Application", boom)


# --- show_arrow_menu: reports unavailable instead of crashing when
# prompt_toolkit can't get a real console; propagates a real
# KeyboardInterrupt (on_ctrl_c="raise") rather than swallowing it as
# "unavailable" -- that's the whole point of the exception= mechanism. ---


def test_show_arrow_menu_falls_back_when_construction_fails(monkeypatch):
    _force_fallback(monkeypatch)
    available, selected = tui.show_arrow_menu("Title", [("a", "A"), ("b", "B")])
    assert available is False
    assert selected is None


def test_show_arrow_menu_propagates_keyboard_interrupt(monkeypatch):
    class FakeApp:
        def run(self):
            raise KeyboardInterrupt()

    monkeypatch.setattr(tui, "Application", lambda **kw: FakeApp())
    try:
        tui.show_arrow_menu("Title", [("a", "A")], on_ctrl_c="raise")
        assert False, "expected KeyboardInterrupt"
    except KeyboardInterrupt:
        pass


# --- pick / pick_index: automatic fallback to a plain numbered picker
# when the arrow-key widget can't be shown -- callers never see
# "unavailable", matching what every menu in this app looked like before
# the arrow-key widget existed. ---


def test_pick_fallback_by_number(monkeypatch):
    _force_fallback(monkeypatch)
    monkeypatch.setattr(builtins, "input", lambda prompt="": "2")
    assert tui.pick("Title", [("a", "A"), ("b", "B")]) == "b"


def test_pick_fallback_blank_returns_default(monkeypatch):
    _force_fallback(monkeypatch)
    monkeypatch.setattr(builtins, "input", lambda prompt="": "")
    assert tui.pick("Title", [("a", "A"), ("b", "B")], default="a") == "a"


def test_pick_fallback_blank_without_default_cancels(monkeypatch):
    _force_fallback(monkeypatch)
    monkeypatch.setattr(builtins, "input", lambda prompt="": "")
    assert tui.pick("Title", [("a", "A"), ("b", "B")]) is None


def test_pick_fallback_out_of_range_returns_none(monkeypatch):
    _force_fallback(monkeypatch)
    monkeypatch.setattr(builtins, "input", lambda prompt="": "99")
    assert tui.pick("Title", [("a", "A"), ("b", "B")]) is None


def test_pick_fallback_non_numeric_returns_none(monkeypatch):
    _force_fallback(monkeypatch)
    monkeypatch.setattr(builtins, "input", lambda prompt="": "abc")
    assert tui.pick("Title", [("a", "A"), ("b", "B")]) is None


def test_pick_index_fallback_returns_int_index(monkeypatch):
    _force_fallback(monkeypatch)
    monkeypatch.setattr(builtins, "input", lambda prompt="": "2")
    assert tui.pick_index("Title", ["Alpha", "Beta"]) == 1


def test_pick_index_fallback_cancel_returns_none(monkeypatch):
    _force_fallback(monkeypatch)
    monkeypatch.setattr(builtins, "input", lambda prompt="": "")
    assert tui.pick_index("Title", ["Alpha", "Beta"]) is None


def test_pick_index_fallback_with_default(monkeypatch):
    _force_fallback(monkeypatch)
    monkeypatch.setattr(builtins, "input", lambda prompt="": "")
    assert tui.pick_index("Title", ["Alpha", "Beta"], default=1) == 1


# --- ask_yes_no: built on pick, with a 2-item Yes/No list. ---


def test_ask_yes_no_fallback_yes(monkeypatch):
    _force_fallback(monkeypatch)
    monkeypatch.setattr(builtins, "input", lambda prompt="": "1")  # "Yes" is item 1
    assert tui.ask_yes_no("Continue?") is True


def test_ask_yes_no_fallback_no(monkeypatch):
    _force_fallback(monkeypatch)
    monkeypatch.setattr(builtins, "input", lambda prompt="": "2")  # "No" is item 2
    assert tui.ask_yes_no("Continue?") is False


def test_ask_yes_no_default_prefills_when_blank(monkeypatch):
    _force_fallback(monkeypatch)
    monkeypatch.setattr(builtins, "input", lambda prompt="": "")
    assert tui.ask_yes_no("Continue?", default=True) is True
    assert tui.ask_yes_no("Continue?", default=False) is False


# --- ask_text / ask_int: always-available, plain input()-based. ---


def test_ask_text_strips_and_returns_input(monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda prompt="": "  hello  ")
    assert tui.ask_text("Name: ") == "hello"


def test_ask_int_blank_uses_default(monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda prompt="": "")
    assert tui.ask_int("Level", default=5) == 5


def test_ask_int_parses_number(monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda prompt="": "7")
    assert tui.ask_int("Level", default=5) == 7


def test_ask_int_non_numeric_falls_back_to_default(monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda prompt="": "abc")
    assert tui.ask_int("Level", default=5) == 5


def test_ask_int_below_min_value_falls_back_to_default(monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda prompt="": "0")
    assert tui.ask_int("Attempts", default=300, min_value=1) == 300


def test_ask_int_min_value_does_not_reject_default_callers(monkeypatch):
    # goal_wizard.py's calls (min_additional_skills etc.) never pass
    # min_value -- 0 must stay a legitimate value there.
    monkeypatch.setattr(builtins, "input", lambda prompt="": "0")
    assert tui.ask_int("Minimum additional", default=1) == 0


def test_ask_text_with_completion_falls_back_to_plain_input_when_no_console(monkeypatch):
    def boom(**kw):
        raise Exception("simulated: no console")

    monkeypatch.setattr(tui, "PromptSession", boom)
    monkeypatch.setattr(builtins, "input", lambda prompt="": "Agitator")
    assert tui.ask_text_with_completion("Skill: ", ["Agitator", "Burst"]) == "Agitator"


# --- color primitives: wrap text in ANSI codes, deliberately not
# routed through prompt_toolkit's output layer (see module docstring),
# so these can never fail regardless of console availability. ---


def test_color_primitives_wrap_text_in_ansi_codes():
    for fn in (tui.accent, tui.dim, tui.good, tui.warn, tui.bad):
        result = fn("hello")
        assert "hello" in result
        assert result != "hello"
        assert result.endswith(tui._RESET)


# --- enable_windows_ansi_colors: classic conhost.exe (cmd.exe's default
# host) doesn't interpret the ANSI codes above at all unless this is
# called first -- confirmed live, they render as literal visible garbage
# otherwise. Must never raise regardless of platform or failure mode. ---


def test_enable_windows_ansi_colors_noop_on_non_windows(monkeypatch):
    monkeypatch.setattr(tui.sys, "platform", "darwin")
    tui.enable_windows_ansi_colors()


def test_enable_windows_ansi_colors_does_not_raise_on_windows(monkeypatch):
    monkeypatch.setattr(tui.sys, "platform", "win32")
    tui.enable_windows_ansi_colors()
