"""Offline tests for hotkeys.HotkeyController's start/stop event logic and
its integration with input.GameInput / state_machine._interruptible_sleep
-- none of these start a real global listener (no Accessibility
permission or actual key presses needed), just exercise the underlying
threading.Event state directly, the same way a real hotkey callback would.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qurio_aug.hotkeys import HotkeyController, StopRequested
from qurio_aug.input import GameInput


def _controller_without_listener():
    """A HotkeyController whose Event state can be driven directly,
    without starting the real pynput.GlobalHotKeys listener thread.
    """
    return HotkeyController.__new__(HotkeyController)


def test_wait_for_start_blocks_until_event_set():
    import threading
    ctrl = _controller_without_listener()
    ctrl._start_event = threading.Event()
    ctrl._stop_event = threading.Event()

    result = {"returned": False}

    def waiter():
        ctrl.wait_for_start()
        result["returned"] = True

    t = threading.Thread(target=waiter, daemon=True)
    t.start()
    time.sleep(0.05)
    assert not result["returned"]  # still blocked

    ctrl._start_event.set()
    t.join(timeout=1.0)
    assert result["returned"]


def test_wait_for_start_consumes_the_event():
    import threading
    ctrl = _controller_without_listener()
    ctrl._start_event = threading.Event()
    ctrl._stop_event = threading.Event()

    ctrl._start_event.set()
    ctrl.wait_for_start()
    assert not ctrl._start_event.is_set()  # cleared after consuming


def test_wait_for_start_returns_false_when_stop_fires_first():
    import threading
    ctrl = _controller_without_listener()
    ctrl._start_event = threading.Event()
    ctrl._stop_event = threading.Event()

    ctrl._stop_event.set()
    assert ctrl.wait_for_start() is False
    assert not ctrl._stop_event.is_set()  # consumed, same as a start-hotkey press


def test_stop_requested_reflects_event_state():
    import threading
    ctrl = _controller_without_listener()
    ctrl._start_event = threading.Event()
    ctrl._stop_event = threading.Event()

    assert not ctrl.stop_requested()
    ctrl._stop_event.set()
    assert ctrl.stop_requested()
    ctrl.reset_stop()
    assert not ctrl.stop_requested()


def test_game_input_press_raises_stop_requested_without_sending_keys():
    presses = []

    class FakeController:
        def press(self, key):
            presses.append(("press", key))

        def release(self, key):
            presses.append(("release", key))

    game = GameInput(controller=FakeController(), post_press_delay=0.0, press_hold=0.0,
                      should_stop=lambda: True)
    try:
        game.confirm()
        assert False, "expected StopRequested"
    except StopRequested:
        pass
    assert presses == []  # no partial keypress sent


def test_game_input_press_proceeds_normally_when_not_stopped():
    presses = []

    class FakeController:
        def press(self, key):
            presses.append(("press", key))

        def release(self, key):
            presses.append(("release", key))

    game = GameInput(controller=FakeController(), post_press_delay=0.0, press_hold=0.0,
                      should_stop=lambda: False)
    game.confirm()
    assert len(presses) == 2  # press + release


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
