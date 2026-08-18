"""Drives the read -> decide -> act loop from STATE 4 (Augmentation
Results screen) using capture.py, ocr.py, decision.py, input.py and
logger.py. See the plan / input.py docstring for the full STATE1..6
walkthrough this is built on.

Entry point (`run`) assumes you've already manually gotten the game to
STATE 1 (Material Select, correct armor piece + augment type chosen) --
menu navigation to get there is out of scope, per the user's design call.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image

from qurio_aug import capture, ocr
from qurio_aug.decision import Decision, Goal, SkillResult, any_profile_reachable, evaluate
from qurio_aug.hotkeys import StopRequested
from qurio_aug.input import POST_PRESS_DELAY, PRESS_HOLD, GameInput
from qurio_aug.logger import AttemptLogger

# Granularity for _interruptible_sleep -- long waits (settle delay, retry
# backoff) are broken into chunks this size so a force-stop hotkey (see
# hotkeys.py) is noticed within ~this long instead of only after the full
# wait completes.
STOP_CHECK_INTERVAL = 0.05


def _interruptible_sleep(seconds: float, should_stop: Callable[[], bool]) -> None:
    remaining = seconds
    while remaining > 0:
        if should_stop():
            raise StopRequested()
        chunk = min(STOP_CHECK_INTERVAL, remaining)
        time.sleep(chunk)
        remaining -= chunk

# How long to wait after a roll lands before the first capture, so the
# "newly changed" sparkle decoration (observed obscuring digits in
# testing -- see ocr.py) has faded and OCR gets a clean read. Applied
# inside read_full_roll itself (not by callers) so both the full `run()`
# loop and a one-shot `evaluate_current_screen` (--dry-run) always get it
# -- evaluate_current_screen used to default to no wait at all, which was
# the actual cause of repeated sparkle-related UnreadableRollErrors during
# dry-run testing even though the roll itself was perfectly readable a
# moment later. Lower-risk to cut than input.py's POST_PRESS_DELAY: worst
# case here is one extra UNREADABLE_RETRY_DELAY spent on a retry, not a
# macro landing mid-transition on the wrong screen -- especially now that
# debridge recovery (see ocr.py) handles most sparkle contamination
# without needing a clean frame at all.
RESULT_SETTLE_DELAY = 0.5

# If a row is STILL unparseable after the initial settle delay (the
# sparkle can apparently outlast it), retry with a fresh capture a few
# more times before giving up -- the game state hasn't changed between
# retries, only time has, which is exactly what a lingering decoration
# needs. Confirmed live: the panel's red border glow is a continuously
# *animated* effect (it pulses, it doesn't just fade once like the
# sparkle), so waiting longer isn't guaranteed to clear it -- sampling
# more often within the retry window matters more than the window being
# long. OCR itself is no longer the bottleneck (contaminated reads
# short-circuit on the run-width check without wasting time on tesseract,
# and clean "1"s hit the ~600x faster template match), so the delay here
# is pure wall-clock waiting for the game's animation state to change,
# not processing time -- more, closer-spaced retries within a similar
# total window raises the odds of landing between sparkle bursts. Retries
# have proven rare in practice (0 UnreadableRollErrors across 100 live
# attempts after the debridge/border-exclusion fixes), so this is tuned
# for "cheap when it does happen" rather than squeezed for speed.
UNREADABLE_RETRY_COUNT = 8
UNREADABLE_RETRY_DELAY = 0.25

# The very first page-indicator check (before any row content is even
# looked at) used to be a single, unretried read -- if the border glow's
# animation happened to corrupt it on that one frame (see
# ocr.indicator_region_ambiguous), read_full_roll permanently committed
# to the "single_page" template for the whole attempt, never rechecking,
# even across 8 rounds of (futile) content retries with the wrong
# template. Confirmed live: a genuinely two-page roll's row0 landed
# squarely on the "Skills" section header, because first_of_multi's rows
# sit further down than single_page's to make room for an indicator the
# code had already decided didn't exist. Same retry cadence as
# UNREADABLE_RETRY_COUNT/DELAY -- only spent when
# ocr.indicator_region_ambiguous flags real content that didn't parse,
# not on every attempt, so a genuinely single-page roll (the common case)
# isn't taxed for a check it doesn't need.
INDICATOR_RETRY_COUNT = 8
INDICATOR_RETRY_DELAY = 0.25

MAX_PAGES = 3
MAX_ATTEMPTS_DEFAULT = 300

LOG_DIR = Path("logs")


class UnreadableRollError(RuntimeError):
    """Raised when a page has content OCR couldn't parse, or the page
    indicator reports a page count we can't reconcile with what's on
    screen -- caller should treat this as a hard stop rather than guess
    and risk a wrong accept/reject action.
    """


def _dump_debug_crops(screenshot: Image.Image, template: list[ocr.RowRegions], page: list[ocr.ParsedRow]) -> list[Path]:
    """Save the full screenshot plus name/value crops for every unparseable
    row on an UnreadableRollError, so the failure can be diagnosed from the
    saved files instead of needing to reproduce it live. Mirrors what
    calibrate.py saves, but only for the row(s) that actually failed.
    """
    LOG_DIR.mkdir(exist_ok=True)
    # Millisecond precision, not just seconds: with the faster timings now
    # in place, two separate UnreadableRollErrors can land in the same
    # clock-second, and second-granularity timestamps used to collide --
    # confirmed live, where a later failure's row crops silently
    # overwrote an earlier failure's under the same filename, leaving a
    # _full.png and its "matching" row crops actually showing two
    # different captures.
    stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
    paths = []

    full_path = LOG_DIR / f"unreadable_{stamp}_full.png"
    screenshot.save(full_path)
    paths.append(full_path)

    for i, (row, parsed) in enumerate(zip(template, page)):
        if not parsed.unparseable:
            continue
        for label, box in (("name", row.name_box), ("value", row.value_box)):
            crop = ocr._crop_fraction(screenshot, box)
            bbox = ocr._bright_bbox(crop)
            if bbox is not None:
                crop = crop.crop(ocr._pad_bbox(bbox, crop))
            path = LOG_DIR / f"unreadable_{stamp}_row{i}_{label}.png"
            ocr._upscale(crop).save(path)
            paths.append(path)

    return paths


def _page_skills(page: list[ocr.ParsedRow]) -> list[SkillResult]:
    return [row.skill for row in page if row.skill is not None]


def _detect_page_indicator(
    screenshot: Image.Image,
    screenshot_fn,
    game: GameInput,
    window_bounds,
    region_config: ocr.RegionConfig,
    should_stop: Callable[[], bool],
    debug_log: Callable[[str], None],
) -> tuple[Image.Image, tuple[int, int] | None]:
    """Reads the page indicator from `screenshot`, retrying with fresh
    captures if the indicator box has content that looks like a real
    (if currently corrupted) indicator per ocr.indicator_region_ambiguous
    -- see INDICATOR_RETRY_COUNT for why this matters: without it, a
    single bad frame permanently commits the whole attempt to the wrong
    row template. Returns the screenshot actually used alongside the
    result, since a retry means the caller's next read should use the
    same (possibly later) screenshot, not the original.
    """
    indicator = ocr.read_page_indicator(screenshot, region_config)
    retries = 0
    while (
        indicator is None
        and ocr.indicator_region_ambiguous(screenshot, region_config)
        and retries < INDICATOR_RETRY_COUNT
    ):
        retries += 1
        debug_log(f"page indicator: ambiguous (content present but unparsed), retry {retries}/{INDICATOR_RETRY_COUNT}")
        _interruptible_sleep(INDICATOR_RETRY_DELAY, should_stop)
        game.park_mouse(window_bounds)
        screenshot = screenshot_fn()
        indicator = ocr.read_page_indicator(screenshot, region_config)
    return screenshot, indicator


def _describe_page(page: list[ocr.ParsedRow]) -> str:
    parts = []
    for row in page:
        if row.skill is not None:
            parts.append(f"{row.skill.name} (removed)" if row.skill.removed else f"{row.skill.name} {row.skill.delta:+d}")
        elif row.blank:
            parts.append("blank")
        else:
            parts.append("UNPARSEABLE")
    return ", ".join(parts)


def _read_page_rows(
    screenshot_fn,
    template: list[ocr.RowRegions],
    game: GameInput,
    window_bounds,
    protected_skills: frozenset[str] = frozenset(),
    should_stop: Callable[[], bool] = lambda: False,
    region_config: ocr.RegionConfig | None = None,
    expected_page: int | None = None,
    debug_log: Callable[[str], None] = lambda msg: None,
) -> list[ocr.ParsedRow]:
    """Reads one page, retrying with a fresh capture if a row comes back
    unparseable -- most commonly caused by the "newly changed" sparkle
    decoration transiently covering a value digit right after a roll
    lands (see ocr.py). Only re-raises (with debug crops saved) if it's
    still unreadable after UNREADABLE_RETRY_COUNT retries.

    Stops retrying early, even with rows still unparseable, the moment a
    protected skill's loss is already confirmed among the rows that DID
    parse: decision.py checks protected skills before anything else, so
    once one is violated the roll is rejected no matter what the still-
    obscured rows would have said -- resolving them is wasted work. This
    is why protected_skills is threaded all the way down here rather than
    only checked by the caller after a page finishes.

    Pass region_config and expected_page (page 2+ reads only, via a
    "continuation" template) to also confirm, on *every* capture -- not
    just once before the first attempt -- that the on-screen page
    indicator actually reads expected_page before trusting template
    against whatever's currently displayed. Without this re-check on
    every retry (not just an upfront one), a next_page() press whose
    transition lands late, or that reverts, can pass an initial check and
    then still get read against the wrong page on a later retry --
    confirmed live: exactly this let a "continuation" read land on page
    1's real "Slots"/"Water Resistance" rows (garbled/blank under page
    2's row coordinates) with a one-shot pre-check already in place. A
    persistently wrong page is reported as its own specific error, not a
    generic unparseable-row one, since the two need different fixes (a
    stuck page-turn vs. a lingering sparkle/glow decoration).

    debug_log (see AttemptLogger.debug) records what each capture and
    retry actually saw -- indicator mismatches, which rows read as what,
    retry counts -- so a failure's full lead-up survives on disk instead
    of only being reconstructable after the fact from a single saved
    screenshot (which is exactly how the last several live failures this
    project hit had to be diagnosed, each taking real back-and-forth to
    pin down).
    """
    page_label = f"page {expected_page}" if expected_page is not None else "page"

    def _capture() -> tuple[Image.Image, list[ocr.ParsedRow] | None, tuple[int, int] | None]:
        game.park_mouse(window_bounds)
        screenshot = screenshot_fn()
        if expected_page is not None:
            indicator = ocr.read_page_indicator(screenshot, region_config)
            if indicator is None or indicator[0] != expected_page:
                got = "no indicator" if indicator is None else f"{indicator[0]}/{indicator[1]}"
                debug_log(f"{page_label}: indicator mismatch, expected {expected_page} got {got}")
                return screenshot, None, indicator
        page = ocr.read_page(screenshot, template)
        debug_log(f"{page_label}: read -- {_describe_page(page)}")
        return screenshot, page, None

    screenshot, page, bad_indicator = _capture()

    def _already_decided() -> bool:
        return page is not None and _protected_violated(_page_skills(page), protected_skills)

    retries = 0
    while (
        (page is None or any(row.unparseable for row in page))
        and not _already_decided()
        and retries < UNREADABLE_RETRY_COUNT
    ):
        retries += 1
        debug_log(f"{page_label}: retry {retries}/{UNREADABLE_RETRY_COUNT}")
        _interruptible_sleep(UNREADABLE_RETRY_DELAY, should_stop)
        screenshot, page, bad_indicator = _capture()

    if page is None:
        got = "no indicator" if bad_indicator is None else f"{bad_indicator[0]}/{bad_indicator[1]}"
        debug_log(f"{page_label}: FAILED -- indicator still {got} after {UNREADABLE_RETRY_COUNT} retries")
        raise UnreadableRollError(
            f"expected to be on page {expected_page}, but the indicator still "
            f"reads {got} after {UNREADABLE_RETRY_COUNT} retries -- the page-turn "
            "keypress may not have registered"
        )

    if any(row.unparseable for row in page) and not _already_decided():
        saved = _dump_debug_crops(screenshot, template, page)
        saved_list = "\n  ".join(str(p) for p in saved)
        debug_log(f"{page_label}: FAILED -- still unparseable after {UNREADABLE_RETRY_COUNT} retries")
        raise UnreadableRollError(
            f"a skill row still couldn't be parsed after {UNREADABLE_RETRY_COUNT} "
            "retries -- refusing to guess. Saved for inspection:\n  " + saved_list
        )
    return page


def _protected_violated(results: list[SkillResult], protected_skills: frozenset[str]) -> bool:
    return any(r.name in protected_skills and r.is_loss for r in results)


def read_full_roll(
    screenshot_fn,
    game: GameInput,
    region_config: ocr.RegionConfig,
    window_bounds,
    goal: Goal,
    settle_delay: float = RESULT_SETTLE_DELAY,
    should_stop: Callable[[], bool] = lambda: False,
    debug_log: Callable[[str], None] = lambda msg: None,
) -> list[SkillResult]:
    """Pages through the skill list (Q/E) reading pages until either
    everything decision-relevant has been seen, or a page 2+ read can be
    skipped outright -- see the early-exit rules below, all confirmed
    safe with the user rather than assumed:

    - Gained/increased skills are capped at 3 per roll, exactly page 1's
      display capacity, so they can *never* land on page 2+ (a roll can
      touch at most 5 skills total -- up to 3 gains plus the 2 the armor
      can carry pre-existing -- and gains are always what's shown first).
      So once page 1 is read, there's nothing further to learn from later
      pages for matching a goal's required/bonus skills -- and if page 1's
      gains alone already can't satisfy *any* profile's required_skills
      (see decision.any_profile_reachable), the roll is doomed regardless
      of what page 2+ contains, so it's skipped outright even if
      protected_skills is configured.
    - Otherwise, the only thing later pages can add is more *removed*
      skills. Those only matter if `goal.protected_skills` is non-empty
      (decision.py ignores non-protected losses entirely) -- so:
        - if protected_skills is empty, page 2+ is skipped unconditionally
          (its only possible content is decision-irrelevant), and
        - if protected_skills is non-empty but a violation already turned
          up on an earlier page, remaining pages are skipped too (the
          roll is already rejected no matter what they contain -- this
          check happens *within* a page too, in _read_page_rows, so a
          confirmed violation also cuts short retrying other still-
          obscured rows on the same page).
      Otherwise (some profile still reachable, protected_skills
      configured, no violation seen yet), every remaining page is read --
      this is the one case where a violation could legitimately be
      waiting on a later page.

    Pagination is driven by the on-screen "<Q N/M E>" indicator (read via
    ocr.read_page_indicator) rather than inferred -- its absence means a
    single page; when present it tells us exactly how many pages exist,
    which layout template to use for page 1 (first_of_multi, since the
    Defense/Slots/Resistance rows are still shown there) vs page 2+
    (continuation, which are not). MAX_PAGES is a sanity cap in case the
    indicator is ever misread as an implausible count.

    That very first indicator read is itself retried (see
    _detect_page_indicator/INDICATOR_RETRY_COUNT) when the indicator box
    has content that looks like a real indicator but didn't parse --
    without this, a single glow-corrupted frame permanently commits the
    whole attempt to the "single_page" template (no re-check happens
    during any later content retry), silently misreading a genuine
    multi-page roll's row0 as whatever real content sits at
    single_page's different row position instead of first_of_multi's --
    confirmed live, that content was the "Skills" section header itself.
    A genuinely single-page roll's indicator box isn't retried needlessly
    for this, since ocr.indicator_region_ambiguous only flags content
    wide enough to plausibly be a real indicator, not the narrow sliver
    of an adjacent row's text a single-page layout's shifted-up rows can
    otherwise bleed into that same box.

    Every page 2+
    capture -- the first attempt and every retry, not just once up front
    -- confirms via _read_page_rows's expected_page that the indicator
    actually shows the page a next_page() press was meant to reach before
    a "continuation" read is trusted against it: a page-turn whose
    transition hasn't landed yet (or reverts mid-retry) still leaves real
    page-1 content on screen, which continuation's row coordinates would
    otherwise silently misread as garbled/blank skill rows instead of
    failing with a clear "the page-turn didn't register" error.

    Waits settle_delay (default RESULT_SETTLE_DELAY) and parks the mouse
    cursor clear of the result panels (see GameInput.park_mouse) before
    the first capture, applied here (rather than left to callers) so both
    the autonomous loop and a one-shot --dry-run evaluation always get it.

    Always leaves the game back on page 1 of STATE4 before returning.

    debug_log (see AttemptLogger.debug) records the indicator read, page
    1's parsed results, the already_rejected/doomed reasoning behind
    whether page 2+ gets read at all, and every navigation step -- this
    is what actually would have shown, in real time, whether a given
    page-2 excursion was legitimate or a false-positive fuzzy-match
    triggering an unnecessary (and here, failure-prone) page turn, rather
    than that having to be reconstructed after the fact from a single
    saved screenshot.
    """
    game.park_mouse(window_bounds)
    _interruptible_sleep(settle_delay, should_stop)

    results: list[SkillResult] = []
    screenshot = screenshot_fn()

    screenshot, indicator = _detect_page_indicator(
        screenshot, screenshot_fn, game, window_bounds, region_config, should_stop, debug_log,
    )
    debug_log(f"page indicator: {indicator}")
    if indicator is None:
        page = _read_page_rows(
            screenshot_fn, region_config.row_templates["single_page"], game, window_bounds,
            goal.protected_skills, should_stop, debug_log=debug_log,
        )
        results.extend(_page_skills(page))
        return results

    current, total = indicator
    if current != 1:
        debug_log(f"FAILED: expected to start on page 1, indicator reads {current}/{total}")
        raise UnreadableRollError(
            f"expected to start pagination on page 1, but the indicator "
            f"reads {current}/{total} -- game state doesn't match assumptions"
        )
    if total > MAX_PAGES:
        debug_log(f"FAILED: implausible page count {total} (cap is {MAX_PAGES})")
        raise UnreadableRollError(
            f"page indicator reports {total} pages, above the sanity cap "
            f"of {MAX_PAGES} -- likely a misread, refusing to guess"
        )

    page = _read_page_rows(
        screenshot_fn, region_config.row_templates["first_of_multi"], game, window_bounds,
        goal.protected_skills, should_stop, debug_log=debug_log,
    )
    page1_results = _page_skills(page)
    results.extend(page1_results)

    already_rejected = _protected_violated(page1_results, goal.protected_skills)
    doomed = not already_rejected and not any_profile_reachable(goal.profiles, page1_results)
    debug_log(
        f"page 1 done -- results: {_describe_page(page)} | "
        f"already_rejected={already_rejected} doomed={doomed} total_pages={total}"
    )
    next_page_presses = 0
    try:
        if not already_rejected and not doomed and goal.protected_skills:
            for page_num in range(2, total + 1):
                debug_log(f"paging forward to page {page_num}")
                game.next_page()
                next_page_presses += 1
                page = _read_page_rows(
                    screenshot_fn, region_config.row_templates["continuation"], game, window_bounds,
                    goal.protected_skills, should_stop,
                    region_config=region_config, expected_page=page_num, debug_log=debug_log,
                )
                page_results = _page_skills(page)
                results.extend(page_results)
                if _protected_violated(page_results, goal.protected_skills):
                    debug_log(f"page {page_num}: protected violation confirmed, stopping early")
                    break  # already rejected -- no need to read further pages
        else:
            debug_log("page 2+ skipped (already_rejected, doomed, or no protected_skills configured)")
    finally:
        # Unconditional, not just on the happy path: if a page 2+ read
        # raises (UnreadableRollError from unreadable content, or
        # StopRequested from a force-stop mid-retry), next_page() presses
        # already sent were about to be abandoned uncleaned -- the game
        # left sitting on whatever page it navigated to, not page 1. The
        # caller assumes read_full_roll always leaves the game back on
        # page 1 (see this function's docstring); on a hard stop that
        # exits the whole process, the *next* invocation's trigger_roll()
        # blindly sends STATE1 keys (Material Select) assuming that's
        # true -- sent into a still-on-page-2 Augmentation Results screen
        # instead, those keys do something else entirely, which is a
        # plausible way one crash's leftover state corrupts the *next*
        # run's very first read. Pressing prev_page() here even while an
        # exception is propagating closes that gap.
        for _ in range(next_page_presses):
            game.prev_page()
        if next_page_presses:
            debug_log(f"navigated back to page 1 ({next_page_presses} prev_page press(es))")

    return results


@dataclass
class RunResult:
    accepted: bool
    attempts: int
    decision: Decision | None
    stopped: bool = False  # True if a force-stop hotkey ended the run early


def evaluate_current_screen(
    goal: Goal,
    *,
    window_title_hint: str | None = None,
    region_config: ocr.RegionConfig | None = None,
    log: AttemptLogger | None = None,
    press_hold: float = PRESS_HOLD,
    post_press_delay: float = POST_PRESS_DELAY,
    settle_delay: float = RESULT_SETTLE_DELAY,
    should_stop: Callable[[], bool] = lambda: False,
) -> Decision:
    """One-shot: read whatever STATE4 roll is currently on screen and
    evaluate it against `goal`, then stop -- never sends the accept/reject/
    reroll macros that would actually change game state. Note this can
    still send Q/E page-turn keypresses if the roll spans multiple pages
    (there's no way to read content that isn't on screen without paging to
    it); it always leaves the game back on page 1 of STATE4 afterward.
    Intended to be invoked once per manually-triggered roll while
    calibrating/validating OCR + decision logic against the real game,
    before trusting `run` to drive it unattended (see main.py --dry-run).

    read_full_roll applies its own settle delay (+ retries on an
    unparseable row) before capturing, so this doesn't need to add one --
    it used to default to no wait at all, which was the actual cause of
    repeated sparkle-related UnreadableRollErrors during dry-run testing.

    press_hold/post_press_delay/settle_delay let you tune input timing
    without editing code (see main.py's matching CLI flags) -- lower
    values speed up a long run but risk a macro's later keypresses
    landing mid-transition on the wrong screen, a failure mode that
    doesn't raise the way an OCR misread does. Validate at a low
    --max-attempts before trusting a faster setting for a long run.

    should_stop is checked before every keypress and during every wait
    (see hotkeys.py) -- raises StopRequested if it fires, which this
    doesn't catch (only `run`'s long loop does; a one-shot evaluation
    being interrupted mid-page-turn is edge-case enough not to need its
    own recovery path).
    """
    region_config = region_config or ocr.load_region_config()
    window = capture.find_game_window(window_title_hint or region_config.window_title_hint)
    game = GameInput(post_press_delay=post_press_delay, press_hold=press_hold, should_stop=should_stop)
    debug_log = log.debug if log is not None else (lambda msg: None)

    def screenshot_fn() -> Image.Image:
        return capture.screenshot_window(window)

    roll = read_full_roll(
        screenshot_fn, game, region_config, window.bounds, goal, settle_delay, should_stop, debug_log,
    )
    decision = evaluate(goal, roll)
    if log is not None:
        print(log.log(decision))
    return decision


def run(
    goal: Goal,
    *,
    window_title_hint: str | None = None,
    region_config: ocr.RegionConfig | None = None,
    max_attempts: int = MAX_ATTEMPTS_DEFAULT,
    press_hold: float = PRESS_HOLD,
    post_press_delay: float = POST_PRESS_DELAY,
    settle_delay: float = RESULT_SETTLE_DELAY,
    should_stop: Callable[[], bool] = lambda: False,
) -> RunResult:
    """Runs the full autonomous reroll loop starting from STATE 1
    (Material Select, correct armor piece + augment type already chosen)
    until `goal` is met, max_attempts is hit, or the force-stop hotkey
    fires: trigger a roll, read it, accept or reroll, repeat -- each
    reroll cycles all the way back through STATE1 before producing the
    next STATE4 result. Validate with main.py --dry-run
    (evaluate_current_screen, called manually per roll) before running
    this unattended.

    See evaluate_current_screen's docstring for the press_hold /
    post_press_delay / settle_delay tuning tradeoffs. should_stop is
    checked before every keypress and during every wait (see hotkeys.py);
    StopRequested is caught here specifically (not left to propagate) so
    a mid-run stop returns a normal RunResult(stopped=True) instead of an
    exception the caller has to handle separately.
    """
    region_config = region_config or ocr.load_region_config()
    window = capture.find_game_window(window_title_hint or region_config.window_title_hint)
    game = GameInput(post_press_delay=post_press_delay, press_hold=press_hold, should_stop=should_stop)
    log = AttemptLogger(goal=goal)

    def screenshot_fn() -> Image.Image:
        return capture.screenshot_window(window)

    decision = None
    attempt = 0
    try:
        game.park_mouse(window.bounds)
        game.trigger_roll()  # STATE1 -> first STATE4 result

        for attempt in range(1, max_attempts + 1):
            roll = read_full_roll(
                screenshot_fn, game, region_config, window.bounds, goal, settle_delay, should_stop,
                log.debug,
            )
            decision = evaluate(goal, roll)
            print(log.log(decision))

            if decision.accepted:
                game.accept_macro()
                return RunResult(True, attempt, decision)
            else:
                game.reroll_macro()
    except StopRequested:
        print(f"\nForce-stopped after {attempt} attempt(s).")
        return RunResult(False, attempt, decision, stopped=True)

    return RunResult(False, max_attempts, decision)
