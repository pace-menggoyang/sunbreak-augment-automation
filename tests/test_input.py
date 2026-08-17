"""Offline tests for GameInput's key macros, using a fake pynput Controller
that just records press/release calls (no real input sent, no game
needed). These exist because a real bug slipped through here once
already: `run()` called read_full_roll assuming a roll was already on
screen, when actually nothing before it ever performed the STATE1 ->
STATE4 transition for the *first* attempt (reroll_macro did it for every
subsequent one, via trigger_roll at its tail)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qurio_aug.input import GameInput, _KEY_MAP


class FakeController:
    def __init__(self):
        self.presses = []

    def press(self, key):
        self.presses.append(key)

    def release(self, key):
        pass


class FakeMouse:
    def __init__(self):
        self.position = None


def _make_game():
    controller = FakeController()
    game = GameInput(controller=controller, post_press_delay=0.0, mouse=FakeMouse())
    return game, controller


def _actions(controller):
    """Map recorded keys back to action names, for readable assertions."""
    reverse = {v: k for k, v in _KEY_MAP.items()}
    return [reverse[k] for k in controller.presses]


def test_trigger_roll_sequence():
    game, controller = _make_game()
    game.trigger_roll()
    assert _actions(controller) == ["autoselect", "confirm", "confirm"]


def test_reroll_macro_full_sequence():
    # Esc, A, F, F, X, F, F -- per the confirmed STATE4..STATE1..STATE4 walkthrough.
    game, controller = _make_game()
    game.reroll_macro()
    assert _actions(controller) == [
        "cancel", "left", "confirm", "confirm", "autoselect", "confirm", "confirm",
    ]


def test_accept_macro_full_sequence():
    # F, A, F, D, F -- per the confirmed STATE4..STATE6a walkthrough.
    game, controller = _make_game()
    game.accept_macro()
    assert _actions(controller) == ["confirm", "left", "confirm", "right", "confirm"]


def test_park_mouse_moves_within_window_bounds():
    game, controller = _make_game()
    bounds = (100.0, 200.0, 1440.0, 900.0)
    game.park_mouse(bounds)
    x, y = game._mouse.position
    assert 100.0 <= x <= 100.0 + 1440.0
    assert 200.0 <= y <= 200.0 + 900.0


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
