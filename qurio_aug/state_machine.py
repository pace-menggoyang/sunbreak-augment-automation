"""Drives the read -> decide -> act loop from STATE 4 (Augmentation
Results screen) using capture.py, ocr.py, decision.py, input.py and
logger.py. See the plan / input.py docstring for the full STATE1..6
walkthrough this is built on.

Entry point (`run`) assumes you've already manually gotten the game to
STATE 1 (Material Select, correct armor piece + augment type chosen) --
menu navigation to get there is out of scope, per the user's design call.
"""
from __future__ import annotations

import sys
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
from qurio_aug.run_stats import RunStats

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
# code had already decided didn't exist. Only spent when
# ocr.indicator_region_ambiguous flags real content that didn't parse,
# not on every attempt, so a genuinely single-page roll (the common case)
# isn't taxed for a check it doesn't need.
#
# Capped at 2 retries, down from 8, on the evidence of ~28,000 ambiguous
# episodes in the Windows beta logs (157 runs): a retry only ever produced
# a parsed indicator within the first 2 (81 episodes: 49 on the 1st, 32 on
# the 2nd) -- the 842 episodes that went past 2 retries never did, and the
# 818 that burned all 8 cost ~2.4s each for nothing.
INDICATOR_RETRY_COUNT = 2
INDICATOR_RETRY_DELAY = 0.25

# An indicator that's still ambiguous after every retry falls back to the
# single_page layout, which in the beta logs read back all-blank 98% of the
# time (803 of 818, vs a 17% baseline for ordinary single-page rolls) --
# i.e. most of these are probably real multi-page rolls being read with the
# wrong template and silently rejected as empty. Root cause unconfirmed
# (no screenshot of one was ever saved), so capture a few for diagnosis.
MAX_UNRESOLVED_INDICATOR_DUMPS = 5

MAX_PAGES = 3
MAX_ATTEMPTS_DEFAULT = 300

# Consecutive rolls that read back with no skills at all. Ordinary single
# empty rolls are common (~17% of rolls in the beta logs, so 30 in a row is
# ~1e-23 by chance); a streak means the bot can't see what the game shows.
# It happened for real: ~5,300 attempts across several sessions read every
# roll as empty and were rejected, unnoticed, because a shortened settle wait
# read the screen before the results had even started to appear. Warn at the
# first, stop at the second -- every reroll spends materials and zenny.
BLANK_STREAK_WARN = 30
BLANK_STREAK_STOP = 100


def _blank_streak_message(streak: int, warning: bool) -> str:
    base = (
        f"{streak} attempts in a row read as empty (no skills at all). About 1 in 6 rolls is "
        "empty, so this is almost certainly a problem, not bad luck: check that the game is on "
        "the Augmentation Results screen, that this armor/augment can roll skills, and that "
        "--settle-delay hasn't been lowered"
    )
    return base + (f" -- the run will stop at {BLANK_STREAK_STOP}." if warning else " -- stopping.")


LOG_DIR = Path("logs")


class UnreadableRollError(RuntimeError):
    """Raised when a page has content OCR couldn't parse, or the page
    indicator reports a page count we can't reconcile with what's on
    screen -- caller should treat this as a hard stop rather than guess
    and risk a wrong accept/reject action.
    """


class BlankStreakError(UnreadableRollError):
    """Raised by run() after BLANK_STREAK_STOP empty rolls in a row. A
    subclass of UnreadableRollError, so main.py already reports it cleanly
    (message plus exit code 2) and dry-run/other callers treat it as a hard
    stop.
    """


_DIALOG_LABELS = {
    ocr.DIALOG_PROCEED: '"Requires materials... Proceed?"',
    ocr.DIALOG_APPLY_RESULTS: '"Apply results?"',
    ocr.DIALOG_ERROR: 'a game error dialog ("A communication error has occurred")',
    ocr.DIALOG_CONTINUE: '"Continue Augmenting armor?"',
}

# Dialogs run() deliberately does NOT try to dismiss. "Continue Augmenting"
# only appears after an apply/reject has already happened, so the macro is
# out of step and a blind Confirm (Yes) would carry on to Material Select
# from an unknown state -- stop and let the user look instead.
_UNRECOVERABLE_DIALOGS = frozenset({ocr.DIALOG_CONTINUE})

# Dialogs that mean a key landed mid-transition -- the failure POST_PRESS_DELAY
# exists to prevent -- so dismissing one also widens the key delay a notch
# (see GameInput.slow_down). A server error isn't a timing problem, and
# "Continue Augmenting" stops the run before this is reached.
_TIMING_DIALOGS = frozenset({ocr.DIALOG_PROCEED, ocr.DIALOG_APPLY_RESULTS})

# How many dialogs in a row run() will dismiss while re-reading the same
# attempt before giving up -- a dialog that keeps coming back means
# something else is wrong (server down, game desynced) and blindly
# pressing keys would just make it worse.
MAX_DIALOG_RECOVERIES = 3


class UnexpectedDialogError(UnreadableRollError):
    """A known modal dialog is covering the skill rows. Subclasses
    UnreadableRollError so callers that don't know about it (dry-run,
    main.py's handlers) still treat it as a clean hard stop; run() catches
    it specifically and tries to dismiss the dialog instead.
    """

    def __init__(self, kind: str, detail: str = ""):
        self.kind = kind
        super().__init__(
            f"{_DIALOG_LABELS.get(kind, kind)} is covering the results screen"
            + (f" -- {detail}" if detail else "")
        )


def _dismiss_dialog(game: GameInput, kind: str) -> None:
    """Gets back to a readable state without ever changing game outcome:
    - Proceed: Yes is the default -- exactly the confirm trigger_roll was
      about to press, so the roll just continues.
    - Apply results: No is the default, and it can appear when a macro
      slipped a key. Cancel (== No) dismisses it without applying
      anything; the roll is then re-read and decided normally.
    - Error: only offers Close.
    """
    if kind == ocr.DIALOG_APPLY_RESULTS:
        game.cancel()
    else:
        game.confirm()


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
    stats: RunStats | None = None,
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
        if stats is not None:
            stats.indicator_retries += 1
        debug_log(f"page indicator: ambiguous (content present but unparsed), retry {retries}/{INDICATOR_RETRY_COUNT}")
        _interruptible_sleep(INDICATOR_RETRY_DELAY, should_stop)
        game.park_mouse(window_bounds)
        screenshot = screenshot_fn()
        indicator = ocr.read_page_indicator(screenshot, region_config)
    if indicator is None and ocr.indicator_region_ambiguous(screenshot, region_config):
        if stats is not None:
            stats.indicator_unresolved += 1
        debug_log(
            "page indicator: UNRESOLVED after retries -- falling back to the single_page layout, "
            "which may be the wrong template for this roll"
        )
        _dump_unresolved_indicator(screenshot, region_config, debug_log)
    return screenshot, indicator


_unresolved_indicator_dumps = 0


def _dump_unresolved_indicator(
    screenshot: Image.Image, region_config: ocr.RegionConfig, debug_log: Callable[[str], None],
) -> None:
    """Saves the full screenshot and the indicator crop for the first few
    unresolved-indicator cases of a process, so the cause can finally be
    diagnosed from real pixels (see MAX_UNRESOLVED_INDICATOR_DUMPS).
    Best-effort: a failure to write never interrupts a run.
    """
    global _unresolved_indicator_dumps
    if _unresolved_indicator_dumps >= MAX_UNRESOLVED_INDICATOR_DUMPS:
        return
    _unresolved_indicator_dumps += 1
    try:
        LOG_DIR.mkdir(exist_ok=True)
        # The per-process counter makes the name unique even if two dumps
        # land in the same millisecond -- a fast machine (or a tiny test
        # image) can, and the second would silently overwrite the first.
        stamp = (
            time.strftime("%Y%m%d-%H%M%S")
            + f"-{int(time.time() * 1000) % 1000:03d}-{_unresolved_indicator_dumps}"
        )
        full = LOG_DIR / f"indicator_unresolved_{stamp}_full.png"
        crop = LOG_DIR / f"indicator_unresolved_{stamp}_indicator.png"
        screenshot.save(full)
        ocr._upscale(ocr._crop_fraction(screenshot, region_config.page_indicator_box)).save(crop)
        debug_log(f"page indicator: saved {full} for inspection")
    except OSError:
        pass


def _confidence_tags(row: ocr.ParsedRow) -> str:
    """"how", not just "what": which of _ocr_single_digit's three methods
    actually produced the digit (template match / a specific tesseract psm
    / last-resort sparkle recovery), whether a merged run needed
    debridging first, and whether the value text (sign, or "None") came
    from the fast color+structure classifier or a Tesseract fallback (see
    ocr._classify_value_fast). Blank rows never read a digit or a value
    at all, so this returns "" there. Surfaced so the next live-failure
    investigation doesn't have to be reverse-engineered from a screenshot
    the way several earlier ones were -- see docs/roadmap.md.
    """
    tags = []
    if row.value_source:
        tags.append(f"value:{row.value_source}")
    if row.digit_source:
        tags.append(row.digit_source)
    if row.debridge != "none":
        tags.append(f"debridged:{row.debridge}")
    if row.name_source:
        tags.append(f"name:{row.name_source}")
    return f" [{', '.join(tags)}]" if tags else ""


def _describe_page(page: list[ocr.ParsedRow]) -> str:
    parts = []
    for row in page:
        if row.skill is not None:
            base = f"{row.skill.name} (removed)" if row.skill.removed else f"{row.skill.name} {row.skill.delta:+d}"
            parts.append(base + _confidence_tags(row))
        elif row.blank:
            parts.append("blank")
        else:
            parts.append("UNPARSEABLE" + _confidence_tags(row))
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
    stats: RunStats | None = None,
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

    When region_config is given (regardless of expected_page -- page 1
    reads pass it too, just not expected_page), a still-unparseable
    failure also checks ocr.is_augmentation_results_screen before raising,
    to distinguish "this is the results screen but a row is genuinely
    unreadable" from "this isn't the results screen at all" -- see that
    check's own docstring for why it's only ever run here, not on every
    read.
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

    def _raise_if_dialog() -> None:
        # A dialog over the rows can never clear on its own, so retrying
        # against it just burns the whole retry budget (~3s) before failing.
        if region_config is None:
            return
        kind = ocr.detect_modal_dialog(screenshot, region_config)
        if kind is not None:
            debug_log(f"{page_label}: modal dialog detected ({kind})")
            raise UnexpectedDialogError(kind)

    if page is not None and any(row.unparseable for row in page) and not _already_decided():
        _raise_if_dialog()

    retries = 0
    while (
        (page is None or any(row.unparseable for row in page))
        and not _already_decided()
        and retries < UNREADABLE_RETRY_COUNT
    ):
        retries += 1
        if stats is not None:
            stats.page_retries += 1
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
        _raise_if_dialog()  # a dialog can also appear mid-retry
        saved = _dump_debug_crops(screenshot, template, page)
        saved_list = "\n  ".join(str(p) for p in saved)
        # Only checked here, at the point a read is already about to fail
        # anyway -- not on every capture -- so a successful attempt (the
        # overwhelming majority) never pays for the extra tesseract call.
        # Confirmed live: a screenshot saved on exactly this failure once
        # turned out to be STATE3 ("Requires materials... Proceed?"), not
        # STATE4 at all -- trigger_roll() (used both to start a run and
        # inside every reroll_macro()) doesn't confirm it actually landed
        # on a fresh result before OCR starts trusting what it reads.
        wrong_screen = (
            region_config is not None
            and not ocr.is_augmentation_results_screen(screenshot, region_config)
        )
        debug_log(
            f"{page_label}: FAILED -- still unparseable after {UNREADABLE_RETRY_COUNT} retries"
            + (" (not even the Augmentation Results screen)" if wrong_screen else "")
        )
        if wrong_screen:
            raise UnreadableRollError(
                f"a skill row still couldn't be parsed after {UNREADABLE_RETRY_COUNT} retries, "
                "and this doesn't even look like the Augmentation Results screen -- possibly "
                "out of materials, a confirmation dialog is still showing, or the game hasn't "
                "finished transitioning. Saved for inspection:\n  " + saved_list
            )
        raise UnreadableRollError(
            f"a skill row still couldn't be parsed after {UNREADABLE_RETRY_COUNT} "
            "retries -- refusing to guess. Saved for inspection:\n  " + saved_list
        )
    if stats is not None:
        stats.record_page(page)
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
    stats: RunStats | None = None,
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
        screenshot, screenshot_fn, game, window_bounds, region_config, should_stop, debug_log, stats,
    )
    debug_log(f"page indicator: {indicator}")
    if indicator is None:
        page = _read_page_rows(
            screenshot_fn, region_config.row_templates["single_page"], game, window_bounds,
            goal.protected_skills, should_stop, region_config=region_config, debug_log=debug_log,
            stats=stats,
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
        goal.protected_skills, should_stop, region_config=region_config, debug_log=debug_log,
        stats=stats,
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
                    stats=stats,
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


def _read_recovering_from_dialogs(
    read: Callable[[], list[SkillResult]],
    game: GameInput,
    debug_log: Callable[[str], None],
    stats: RunStats | None = None,
) -> list[SkillResult]:
    """Runs `read`, dismissing a covering modal dialog (see
    _dismiss_dialog) and re-reading the same roll, up to
    MAX_DIALOG_RECOVERIES times in a row before re-raising.
    """
    recoveries = 0
    while True:
        try:
            return read()
        except UnexpectedDialogError as e:
            if e.kind in _UNRECOVERABLE_DIALOGS:
                raise UnexpectedDialogError(
                    e.kind, "the key sequence is out of step, so it wasn't dismissed automatically"
                ) from e
            if recoveries >= MAX_DIALOG_RECOVERIES:
                raise UnexpectedDialogError(
                    e.kind, f"still there after dismissing it {recoveries} times, stopping"
                ) from e
            recoveries += 1
            debug_log(f"dismissing dialog {e.kind} ({recoveries}/{MAX_DIALOG_RECOVERIES})")
            _dismiss_dialog(game, e.kind)
            if stats is not None:
                stats.dialogs[e.kind] += 1
            if e.kind in _TIMING_DIALOGS:
                slowed = game.slow_down()
                if slowed is not None:
                    debug_log(f"key delay raised {slowed[0]:.3f}s -> {slowed[1]:.3f}s after a stray dialog")
                    if stats is not None:
                        stats.record_slowdown(*slowed)


@dataclass
class RunResult:
    accepted: bool
    attempts: int
    decision: Decision | None
    stopped: bool = False  # True if a force-stop hotkey ended the run early


# Wide enough for the longest realistic line ("attempt 300/300  |  999.9/min
# |  elapsed 999m59s" is 49 chars) with margin -- padded to this width so an
# in-place \r update never leaves a trailing fragment of a longer previous
# line on screen (e.g. the moment attempt count gains a digit).
_PROGRESS_LINE_WIDTH = 70


def _format_progress(attempt: int, max_attempts: int, elapsed: float) -> str:
    """One-line progress readout for a long run -- attempt count, a rough
    attempts/min rate, and elapsed time -- meant to replace a full
    per-attempt log line scrolling past for every single reject, which is
    most of a long run's output and rarely worth reading live (the full
    detail is always in the .log/.jsonl files regardless -- see
    AttemptLogger). run() only uses this in place of the real per-attempt
    line for a "boring" (non-accepted, non-suspicious) attempt; see run's
    own docstring/body for when it falls back to the full line instead.
    """
    rate = attempt / elapsed * 60 if elapsed > 0 else 0.0
    mins, secs = divmod(int(elapsed), 60)
    text = f"attempt {attempt}/{max_attempts}  |  {rate:.1f}/min  |  elapsed {mins}m{secs:02d}s"
    return text.ljust(_PROGRESS_LINE_WIDTH)


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


def _emit_run_summary(stats: RunStats, log: AttemptLogger, elapsed: float) -> None:
    """Prints the run summary and writes the same lines to the debug log,
    so a bug report's log is self-explanatory. Skipped only for a run where
    nothing at all happened -- one that dies on its very first attempt still
    gets one, since the retries it burned are exactly what's worth seeing.
    """
    if log.attempt == 0 and not (stats.dialogs or stats.page_retries or stats.indicator_retries):
        return
    lines = stats.summary_lines(log.attempt, elapsed)
    print("\n".join(lines))
    for line in lines:
        log.debug(line)


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

    Console output during the loop is a live, in-place progress readout
    (attempt N/max, rough rate, elapsed time -- see _format_progress)
    rather than one full decision line scrolling past per attempt, since
    almost every attempt in a long run is an uninteresting reject and the
    full detail is always in AttemptLogger's .log/.jsonl files regardless.
    An accepted attempt, or one flagged suspicious (possible OCR misread,
    per logger.format_line), always prints its full line instead, since
    those are exactly the moments worth seeing live. Only kicks in when
    stdout is a real terminal -- redirected to a file/pipe, an in-place
    \r update would just be unreadable noise, so every attempt prints its
    full line there, unchanged from before this existed.
    """
    region_config = region_config or ocr.load_region_config()
    window = capture.find_game_window(window_title_hint or region_config.window_title_hint)
    game = GameInput(post_press_delay=post_press_delay, press_hold=press_hold, should_stop=should_stop)
    log = AttemptLogger(goal=goal)

    def screenshot_fn() -> Image.Image:
        return capture.screenshot_window(window)

    decision = None
    attempt = 0
    live_progress = sys.stdout.isatty()
    dangling = False  # True if stdout's last write has no trailing newline (an in-place progress update)
    start_time = time.monotonic()
    stats = RunStats()
    blank_streak = 0
    try:
        game.park_mouse(window.bounds)
        game.trigger_roll()  # STATE1 -> first STATE4 result

        for attempt in range(1, max_attempts + 1):
            roll = _read_recovering_from_dialogs(
                lambda: read_full_roll(
                    screenshot_fn, game, region_config, window.bounds, goal, settle_delay,
                    should_stop, log.debug, stats,
                ),
                game, log.debug, stats,
            )
            decision = evaluate(goal, roll)
            line = log.log(decision)
            notable = decision.accepted or any(r.is_suspicious for r in decision.added + decision.removed)

            if live_progress and not notable:
                elapsed = time.monotonic() - start_time
                sys.stdout.write("\r" + _format_progress(attempt, max_attempts, elapsed))
                sys.stdout.flush()
                dangling = True
            else:
                if dangling:
                    sys.stdout.write("\n")
                    dangling = False
                print(line)

            # A roll with no skills at all is ordinary (~17% of rolls in the
            # beta logs), but a long unbroken streak never is -- it means the
            # bot is blind (wrong screen, an armor/augment that can't roll
            # skills, a read before the rows are drawn). Warn, then stop
            # before the next reroll spends more materials.
            blank_streak = blank_streak + 1 if not roll else 0
            stats.blank_attempts += 1 if not roll else 0
            stats.max_blank_streak = max(stats.max_blank_streak, blank_streak)
            if blank_streak == BLANK_STREAK_WARN:
                if dangling:
                    sys.stdout.write("\n")
                    dangling = False
                message = _blank_streak_message(blank_streak, warning=True)
                log.debug(message)
                print(message)
            if blank_streak >= BLANK_STREAK_STOP:
                message = _blank_streak_message(blank_streak, warning=False)
                log.debug(message)
                raise BlankStreakError(message)

            if decision.accepted:
                game.accept_macro()
                return RunResult(True, attempt, decision)
            else:
                game.reroll_macro()
    except StopRequested:
        if dangling:
            sys.stdout.write("\n")
            dangling = False
        print(f"\nForce-stopped after {attempt} attempt(s).")
        return RunResult(False, attempt, decision, stopped=True)
    finally:
        # However the run ends -- accepted, gave up, force-stopped, or an
        # UnreadableRollError propagating out -- close off the in-place
        # progress line and leave a summary behind.
        if dangling:
            sys.stdout.write("\n")
            dangling = False
        _emit_run_summary(stats, log, time.monotonic() - start_time)

    return RunResult(False, max_attempts, decision)
