"""Offline tests for the timing probe. A simulated game stands in for the real
one: a fake clock advances 40ms per screenshot, and each screen draws its
regions at a known time after the key press, so the probe's measurements can
be checked against the truth."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw

from qurio_aug import ocr, probe
from qurio_aug.hotkeys import StopRequested

CONFIG = ocr.load_region_config()
SIZE = (480, 270)  # small -- the boxes are fractions, so the numbers still hold
FRAME_INTERVAL = 0.04

# When each part of the results screen is drawn after the results-step key
# press, in seconds. The constant "Skills" header comes with the title; the
# skill values come last.
TITLE_AT, STATS_AT, SKILLS_AT = 0.30, 0.50, 0.60
GREEN = (30, 200, 60)
WHITE = (240, 240, 240)
HEADER = (200, 180, 90)


class FakeGame:
    """Fake clock + fake screen. Each capture advances the clock one frame."""

    def __init__(self, skills_visible=True):
        self.t = 0.0
        self.press_t = 0.0
        self.step = -1
        self.skills_visible = skills_visible

    def now(self):
        return self.t

    def press(self, action):
        self.step += 1
        self.press_t = self.t
        self.t += 0.03  # the key hold

    def capture(self):
        self.t += FRAME_INTERVAL
        dt = self.t - self.press_t
        img = Image.new("RGB", SIZE, (20, 20, 20))
        draw = ImageDraw.Draw(img)
        boxes = probe.region_boxes(CONFIG)

        def fill(box, color):
            x0, y0, x1, y1 = box
            draw.rectangle((x0 * SIZE[0], y0 * SIZE[1], x1 * SIZE[0], y1 * SIZE[1]), fill=color)

        if probe.PROBE_STEPS[self.step % len(probe.PROBE_STEPS)][0] == probe.RESULTS_STEP:
            if dt >= TITLE_AT:
                fill(boxes["title"], WHITE)
                # The constant "Skills" header: inside the skill-rows box, but above and left of the values.
                x0, y0, x1, _ = boxes["skill_rows"]
                fill((x0, y0, x0 + (x1 - x0) * 0.5, y0 + 0.02), HEADER)
            if dt >= STATS_AT:
                fill(boxes["stat_rows"], WHITE)
            if dt >= SKILLS_AT and self.skills_visible:
                fill(probe.value_column_box(CONFIG), GREEN)
        elif dt >= 0.12:
            fill(boxes["dialog"], WHITE)  # an ordinary modal/screen appearing a bit after the key
        return img


def _run(game, out_dir, cycles=1, **kw):
    return probe.run_probe(
        capture_fn=game.capture, press_fn=game.press, region_config=CONFIG, out_dir=out_dir,
        now=game.now, cycles=cycles, **kw,
    )


def _results_step(report):
    return next(s for s in report.steps if s.name == probe.RESULTS_STEP)


def test_it_measures_when_each_region_stops_changing_within_a_frame():
    with tempfile.TemporaryDirectory() as d:
        report = _run(FakeGame(), Path(d))
    step = _results_step(report)
    tol = FRAME_INTERVAL * 1000 * 1.5  # within about a frame of the truth
    settle = step.region_settle_ms
    assert abs(settle["title"] - TITLE_AT * 1000) <= tol, settle
    assert abs(settle["stat_rows"] - STATS_AT * 1000) <= tol, settle
    assert abs(settle["skill_rows"] - SKILLS_AT * 1000) <= tol, settle
    # The whole screen is done changing when the last part is drawn.
    assert abs(step.settle_ms - SKILLS_AT * 1000) <= tol, step.settle_ms
    assert step.had_skills and step.had_title


def test_the_constant_skills_header_does_not_read_as_the_rows_being_ready():
    # THE trap: the header sits inside the skill-rows box and appears with the
    # title (300ms). If it counted, the rows would read "ready" ~300ms too
    # early -- and a wait cut to that value would blank every roll again.
    with tempfile.TemporaryDirectory() as d:
        step = _results_step(_run(FakeGame(), Path(d)))
    assert step.region_settle_ms["skill_rows"] > (TITLE_AT + 0.2) * 1000


def test_every_step_of_the_cycle_is_probed_in_order():
    with tempfile.TemporaryDirectory() as d:
        report = _run(FakeGame(), Path(d))
    assert [s.name for s in report.steps] == [name for name, _, _ in probe.PROBE_STEPS]
    assert [s.action for s in report.steps] == [action for _, action, _ in probe.PROBE_STEPS]
    assert all(s.frames > 5 for s in report.steps)


def test_a_roll_with_no_skills_is_reported_and_not_counted():
    with tempfile.TemporaryDirectory() as d:
        report = _run(FakeGame(skills_visible=False), Path(d))
        text = Path(report.summary_path).read_text()
    step = _results_step(report)
    assert not step.had_skills and step.had_title
    assert "no skills on this roll" in text
    assert "no roll had skills -- run the probe again to catch one" in text


def test_it_writes_a_csv_a_summary_and_one_screenshot_per_step():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        report = _run(FakeGame(), d)
        assert report.csv_path.is_file() and report.summary_path.is_file()
        assert len(report.images) == len(probe.PROBE_STEPS)
        assert all(p.is_file() for p in report.images)
        header = report.csv_path.read_text().splitlines()[0]
        assert header.startswith("cycle,step,key,t_ms,diff_to_final,diff_title,diff_stat_rows,diff_skill_rows")
        assert header.endswith("diff_skill_rows,diff_dialog")
        text = report.summary_path.read_text()
    assert "Skill rows on the results screen" in text
    assert "latest across 1 roll(s) with skills" in text
    assert f"~{probe.CURRENT_READ_DELAY_MS} ms" in text


def test_several_cycles_report_one_result_per_roll():
    with tempfile.TemporaryDirectory() as d:
        report = _run(FakeGame(), Path(d), cycles=3)
        text = report.summary_path.read_text()
    assert len(report.steps) == 3 * len(probe.PROBE_STEPS)
    assert "roll 1:" in text and "roll 3:" in text  # one line per roll in the summary
    assert "latest across 3 roll(s) with skills" in text


def test_screenshots_can_be_skipped():
    with tempfile.TemporaryDirectory() as d:
        report = _run(FakeGame(), Path(d), save_images=False)
    assert report.images == []


def test_a_stop_request_aborts_the_probe():
    with tempfile.TemporaryDirectory() as d:
        try:
            _run(FakeGame(), Path(d), should_stop=lambda: True)
            assert False, "expected StopRequested"
        except StopRequested:
            pass


def test_value_column_is_clear_of_the_skills_header():
    # The header is centered on the panel (~0.47-0.53 of the width); the values
    # must start to its right, or the "had skills" check would see the header.
    x0, y0, _, _ = probe.value_column_box(CONFIG)
    assert x0 >= 0.53
    assert y0 >= 0.53  # and below the header's own row (~0.52)


def test_skill_value_pixels_only_count_gain_loss_and_maxed_colours():
    assert probe._is_skill_value_pixel(30, 200, 60)    # green gain
    assert probe._is_skill_value_pixel(200, 60, 60)    # red loss
    assert probe._is_skill_value_pixel(193, 143, 48)   # orange maxed gain
    assert not probe._is_skill_value_pixel(240, 240, 240)  # white text
    assert not probe._is_skill_value_pixel(200, 180, 90)   # the gold "Skills" header
    assert not probe._is_skill_value_pixel(20, 60, 20)     # too dark to be a digit


def test_analyze_step_with_too_few_frames_does_not_crash():
    step = probe.analyze_step("x", "confirm", [])
    assert step.frames == 0 and step.settle_ms is None and step.region_settle_ms == {}


def test_analyze_step_ignores_steady_flicker_when_judging_when_it_settled():
    # The panel border glow animates forever; a tiny flicker must not read as "still changing".
    gray_a = Image.new("L", probe._DIFF_SIZE, 100)
    gray_b = Image.new("L", probe._DIFF_SIZE, 101)
    frames = [
        probe.Frame(t=i * 0.04, gray=(gray_a if i % 2 else gray_b), crops={})
        for i in range(10)
    ]
    step = probe.analyze_step("x", "confirm", frames)
    assert step.settle_ms == 0.0  # settled from the very first frame


# --- main._run_probe: the command wiring (hotkeys, window, key sender) ---


class _FakeHotkeys:
    def __init__(self, start_ok=True):
        self.start_ok = start_ok

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def wait_for_start(self):
        return self.start_ok

    def stop_requested(self):
        return False


class _FakeInput:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.parked = None

    def park_mouse(self, bounds):
        self.parked = bounds

    def press(self, action):
        pass


def _patched_main(start_ok=True, run_probe=None):
    """Swaps main's boundaries for fakes; returns (main, restore, record)."""
    from types import SimpleNamespace
    from qurio_aug import main

    record = SimpleNamespace(game=None, probe_kwargs=None)
    originals = (main.capture.find_game_window, main.HotkeyController, main.GameInput, main.probe.run_probe)

    def make_input(**kwargs):
        record.game = _FakeInput(**kwargs)
        return record.game

    def fake_run_probe(**kwargs):
        record.probe_kwargs = kwargs
        if run_probe is not None:
            return run_probe(**kwargs)
        return probe.ProbeReport()

    main.capture.find_game_window = lambda hint: SimpleNamespace(bounds=(0, 0, 100, 100))
    main.HotkeyController = lambda start, stop: _FakeHotkeys(start_ok)
    main.GameInput = make_input
    main.probe.run_probe = fake_run_probe

    def restore():
        main.capture.find_game_window, main.HotkeyController, main.GameInput, main.probe.run_probe = originals

    return main, restore, record


def test_run_probe_cancelled_before_start_never_touches_the_game():
    main, restore, record = _patched_main(start_ok=False)
    try:
        assert main._run_probe("Monster Hunter Rise", CONFIG, rolls=2) == 130
    finally:
        restore()
    assert record.probe_kwargs is None  # no key was ever sent, no roll spent


def test_run_probe_runs_the_requested_rolls_with_a_no_sleep_key_sender():
    main, restore, record = _patched_main()
    try:
        assert main._run_probe("Monster Hunter Rise", CONFIG, rolls=4) == 0
    finally:
        restore()
    assert record.probe_kwargs["cycles"] == 4
    assert record.probe_kwargs["press_fn"] == record.game.press
    # press() must return the instant the key is released, or the screenshots start late.
    assert record.game.kwargs["post_press_delay"] == 0.0
    assert record.game.parked == (0, 0, 100, 100)  # cursor moved off the panels first


def test_run_probe_reports_a_stop_without_crashing():
    def stop(**kwargs):
        raise StopRequested()

    main, restore, record = _patched_main(run_probe=stop)
    try:
        assert main._run_probe("Monster Hunter Rise", CONFIG, rolls=2) == 130
    finally:
        restore()


def test_probe_is_a_menu_item_and_a_known_command():
    from qurio_aug import main
    assert "probe" in [cmd_id for cmd_id, _ in main._MENU_ITEMS]
    assert "probe" in {name for names, _ in main._COMMANDS for name in names}


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
