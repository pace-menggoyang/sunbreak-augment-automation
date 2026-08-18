# Roadmap

Candidate work for the next milestone(s), ordered high to low priority
within each tier. Nothing here is committed -- it's a menu to pick from,
not a plan.

## High priority

Safety-relevant or blocking issues. These protect a long unattended run
from silently getting stuck, or are prerequisites for trusting the tool
in front of the wider community.

1. **Real Windows beta testing.** Everything in the current release is
   CI-verified (the build succeeds, `--selfcheck` passes on a GitHub
   Actions runner) but nobody has run it against the actual game on real
   Windows hardware yet. This is the single biggest open unknown --
   window-title matching against a native (non-CrossOver) install, DPI
   scaling on a real display, and `pynput` keystroke delivery into a
   native Win32/DirectX window are all unverified. See
   `docs/windows-beta-checklist.md`. Every other item below is
   speculative until this happens.

2. **Detect an out-of-materials / stuck state.** Discussed early in this
   project and deferred; still not built. If materials run out mid-run,
   the bot currently has no way to notice -- it would just keep failing
   the same macro sequence. A farming session can run for hours
   unattended, so silently spinning on a broken macro instead of
   stopping with a clear message is a real risk, not a hypothetical one.

3. **A general "are we on the screen we expect" safeguard.** The
   page-indicator verification added this milestone (`_detect_page_indicator`
   / `_read_page_rows`'s `expected_page`) fixes one specific instance of
   this class of bug. The same underlying risk exists anywhere else the
   code assumes a screen transition landed (e.g. after `trigger_roll()`,
   after `accept_macro()`/`reroll_macro()`) without confirming it. A
   generalized version of the same technique -- check for an expected
   on-screen marker before trusting the next read -- would close off
   whole future bug classes instead of one at a time as they're
   discovered live.

## Medium priority

Clear value, no blocking dependency, moderate effort. Mostly about
lowering friction for non-technical users and for whoever ends up
supporting them.

4. **Guided/interactive calibration.** Calibrating today means running
   `qurio-aug-calibrate`, eyeballing saved crop PNGs in `logs/`, and
   hand-editing fractional boxes in `configs/regions.yaml` until they
   line up. This is the last step in the whole flow that still requires
   real technical comfort. A guided version (step through each region,
   confirm "does this look right?", nudge with arrow-key-style input)
   would remove it.

5. **Chain the first-run flow.** The interactive menu (shipped this
   milestone) offers the right options, but a brand-new user still has
   to know to run them in order: selfcheck, then wizard, then calibrate,
   then dry-run, then start. A "first time setup" menu option that walks
   through that sequence automatically, explaining each step as it goes,
   would remove the remaining "what do I actually do first" confusion.

6. **Edit an existing goal from the menu**, not just create new ones.
   The wizard only writes new files; tweaking a level requirement or
   adding a skill to an allowed pool currently means hand-editing YAML.

7. **A "package up my last failure" command.** Bundles the most recent
   debug log + saved screenshots into one file a community member can
   attach to a bug report, without needing to know which files in `logs/`
   are the relevant ones or how to read them.

8. **Confidence tagging in the debug log.** Right now the debug log
   records what each row parsed as, but not *how* (template match vs.
   tesseract vs. debridged-from-contamination vs. a retry that
   recovered). Surfacing that would make the next live-failure
   investigation faster than this session's already was -- several of
   this milestone's fixes started from reasoning backward through
   screenshots to reconstruct what the OCR pipeline must have done.

## Low priority

Speculative, reactive, or cost money. Worth having on the list, not
worth prioritizing over the above.

9. **More digit templates.** Only "1" and "2" have the fast pixel-match
   path today, on the assumption (confirmed by the user's play
   experience) that augment deltas are always ±1 or ±2. Only becomes
   necessary if a higher delta is ever actually observed live --
   reactive work, not proactive.

10. **Self-calibrating OCR thresholds.** `BRIGHT_THRESHOLD`,
    `MIN_MATCH_SCORE`, `INDICATOR_AMBIGUOUS_WIDTH_FRACTION`, and similar
    constants are all tuned against captures from one machine's display.
    A future monitor with different gamma/brightness could need
    different values. No evidence yet that this is actually a problem
    cross-machine -- worth revisiting only if a beta tester's failures
    look threshold-related.

11. **A visible progress readout during a long run** (attempt N/max,
    rough rate, elapsed time) instead of scrolling per-attempt text.

12. **A sound or system notification on accept**, so a long unattended
    run doesn't require watching the terminal to notice it finished.

13. **Code signing.** Removes the Gatekeeper/SmartScreen warnings
    entirely. Costs real money (~$100-400/yr depending on platform) and
    isn't a code change -- a project/budget decision, not engineering
    work.
