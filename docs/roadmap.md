# Roadmap

Candidate work for the next milestone(s), ordered high to low priority
within each tier. Nothing here is committed -- it's a menu to pick from,
not a plan. See `CHANGELOG.md` for what's already shipped.

## High priority

Safety-relevant or blocking issues. These protect a long unattended run
from silently getting stuck, or are prerequisites for trusting the tool
in front of the wider community.

1. **Real Windows beta testing -- core pipeline fully confirmed working
   end-to-end, three real bugs found and fixed.** First real run against
   real Windows hardware + the live game (see
   `docs/windows-beta-checklist.md` and the [Unreleased] section of
   `CHANGELOG.md`) found and fixed: `--list-windows` crashing on a
   non-cp1252 window title, window owner-name matching silently never
   working (`win32process.OpenProcess` doesn't exist), and the default
   Control+M/N hotkeys colliding with Sunbreak's own Skill Info/Compare
   Equipment binds (now Alt+M/N on Windows). Every stage confirmed
   working against the live game at native 1920x1080: window-title
   matching against a native (non-CrossOver) install, calibration,
   single- and multi-page OCR, `pynput` Q/E page-turn keystroke delivery,
   the decision engine, and -- across two real (non-`--dry-run`) runs --
   both the reject/reroll macro path (5 attempts against the user's
   actual `gila-minmax` goal, each a genuinely different roll, including
   a correct real protected-skill-violation rejection on attempt #1) and
   the accept/confirm macro (a throwaway "match any single skill gain"
   goal accepted and confirmed for real on attempt #1). Every macro this
   tool sends is now verified to actually land on real Windows hardware.

   **Still open:** DPI scaling on a display running at other than 100%
   (this session's display scale wasn't recorded); and a longer sustained
   run (dozens+ attempts, vs. 5) to rule out anything that only shows up
   over time (e.g. the memory leaks already fixed on macOS in
   v0.1.3-beta -- unconfirmed whether Windows has any analogous ones). Both
   free checks from `docs/ocr-performance-research.md` are done: the
   vendored Windows `eng.traineddata` is already `tessdata_fast` (#5, no
   action needed), and the `tesserocr` wheel works and shipped (#1b, see
   the [Unreleased] section of `CHANGELOG.md`).

2. **A page that reads back entirely blank can be a silently wrong/
   occluded screen, and there's no cheap way to tell.** Found live during
   Windows testing (item 1): the Windows capture backend grabs a fixed
   screen region, not the window itself (see `capture/_windows.py`), so
   anything drawn on top mid-run (another window, a notification) can get
   captured instead of the game with no error -- just wrong OCR input.
   When that happens to land on a moment none of the 3 skill rows have
   any bright content, the page reads back as a completely ordinary
   "empty roll" (`ocr.ParsedRow(blank=True, unparseable=False)` for all
   3 rows) and gets evaluated/rejected/rerolled as if it were real,
   silently -- or worse, could misjudge an actual good roll as blank
   and reroll it away, never having genuinely evaluated it.

   The obvious fix -- retry when all rows are blank, using
   `is_augmentation_results_screen`'s title-banner check to tell a
   genuine "no skill changed" roll apart from a wrong/occluded screen --
   can't just run unconditionally: `regular`-type augments legitimately
   produce an all-blank page whenever a roll only touches Defense/Slots/
   Resistance, confirmed by the user to be **fairly common in practice**,
   not a rare edge case, so retrying every time would add real, felt
   overhead to normal farming.

   **A cheap pre-check was tried and empirically disproven -- don't
   re-attempt it blind.** The obvious "free" gate (a pure pixel scan,
   `_bright_bbox`, on just the title-banner region -- cheap since it
   skips Tesseract entirely) assumed an occluding window would be dark in
   that spot. Tested directly against a real occlusion capture from this
   same session (VS Code covering the game): the cheap check returned
   "not blank" -- VS Code's own UI (chat text, syntax highlighting) has
   plenty of bright pixels there too, so the assumption doesn't hold for
   a real productivity app, only for a coincidentally-dark occluder. The
   only signal that actually caught the real capture was the existing,
   full Tesseract-based `is_augmentation_results_screen` (a real text
   match, not a brightness check) -- which costs ~100-200ms and would be
   paid on every all-blank read if used directly, unacceptable given how
   common that read is. Decided (2026-08-23) to drop the fix rather than
   ship a felt-cost tax with no proven-cheap way to gate it -- see the
   git history around that date for the attempted (then reverted)
   implementation in `qurio_aug/ocr.py` / `qurio_aug/state_machine.py` if
   picking this back up. A real fix needs either a genuinely cheap and
   reliable signal that hasn't been found yet, or an explicit decision
   that the ~100-200ms cost is worth paying on every blank read.

## Medium priority

Clear value, no blocking dependency, moderate effort. Mostly about
lowering friction for non-technical users and for whoever ends up
supporting them.

3. **Guided/interactive calibration.** Calibrating today means running
   `qurio-aug-calibrate`, eyeballing saved crop PNGs in `logs/`, and
   hand-editing fractional boxes in `configs/regions.yaml` until they
   line up. This is the last step in the whole flow that still requires
   real technical comfort. A guided version (step through each region,
   confirm "does this look right?", nudge with arrow-key-style input)
   would remove it.

4. **Chain the first-run flow.** The interactive menu (shipped in
   0.1.2-beta) offers the right options, but a brand-new user still has
   to know to run them in order: selfcheck, then wizard, then calibrate,
   then dry-run, then start. A "first time setup" menu option that walks
   through that sequence automatically, explaining each step as it goes,
   would remove the remaining "what do I actually do first" confusion.

## Low priority

Speculative, reactive, or cost money. Worth having on the list, not
worth prioritizing over the above.

5. **More digit templates.** Only "1" and "2" have the fast pixel-match
   path today, on the assumption (confirmed by the user's play
   experience) that augment deltas are always ±1 or ±2. Only becomes
   necessary if a higher delta is ever actually observed live --
   reactive work, not proactive.

6. **Replace value-text OCR with color + structure analysis.** From
   `docs/ocr-performance-research.md` #3: the delta's color already
   encodes gain (green) vs. gain-at-max-level (orange) vs. loss/None
   (red), and "None" vs. a numeric loss is distinguishable by run
   structure, without needing OCR at all for the common case. Real,
   measured color separation, no new dependency, and the same
   green-channel-dominance technique already shipped for sparkle
   recovery (see `_is_green_digit_pixel`) extends naturally here.
   Tesseract call batching (the item that had displaced this in
   priority) turned out to be a measured regression on real hardware and
   was reverted -- see `docs/ocr-performance-research.md` #4c -- so this
   is back to being the most promising remaining OCR speed idea, not a
   fallback.

7. **Self-calibrating OCR thresholds.** `BRIGHT_THRESHOLD`,
   `MIN_MATCH_SCORE`, `INDICATOR_AMBIGUOUS_WIDTH_FRACTION`, and similar
   constants are all tuned against captures from one machine's display.
   A future monitor with different gamma/brightness could need
   different values. No evidence yet that this is actually a problem
   cross-machine -- worth revisiting only if a beta tester's failures
   look threshold-related.

8. **A visible progress readout during a long run** (attempt N/max,
   rough rate, elapsed time) instead of scrolling per-attempt text.

9. **A sound or system notification on accept**, so a long unattended
   run doesn't require watching the terminal to notice it finished.

10. **Code signing.** Removes the Gatekeeper/SmartScreen warnings
    entirely. Costs real money (~$100-400/yr depending on platform) and
    isn't a code change -- a project/budget decision, not engineering
    work.
