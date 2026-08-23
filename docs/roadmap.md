# Roadmap

Candidate work for the next milestone(s), ordered high to low priority
within each tier. Nothing here is committed -- it's a menu to pick from,
not a plan. See `CHANGELOG.md` for what's already shipped.

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
   speculative until this happens. While there's Windows access, also
   worth two free checks from `docs/ocr-performance-research.md`: (a)
   which `eng.traineddata` variant the vendored Windows tesseract ships
   (macOS already uses the fast one; if Windows doesn't, swapping it in
   is a same-order-of-magnitude speedup for zero packaging risk -- #5 in
   that doc), and (b) whether the prebuilt `tesserocr` Windows wheel from
   `simonflueckiger/tesserocr-windows_build` actually imports and runs
   (#1a) -- both are 5-10 minute checks, not new work, worth doing in the
   same sitting as the rest of the beta testing.

## Medium priority

Clear value, no blocking dependency, moderate effort. Mostly about
lowering friction for non-technical users and for whoever ends up
supporting them.

2. **Batch a page's Tesseract calls into one subprocess invocation**,
   via tesseract's native `imagelist` mode rather than one call per
   name/value crop. The single most-validated performance finding in
   `docs/ocr-performance-research.md` (#4a/#4b): measured ~1.1-1.2x
   faster against the already-shipped threaded baseline, with
   byte-identical output confirmed across three different real captures
   (border-glow, continuation-layout, and a genuine blank-slot case, not
   just one clean reference). No new dependency, no Windows-specific
   risk -- ready to implement, not just research; what's left is
   breadth (exercising it against more live sessions), not a known
   correctness gap. Slated for 0.1.3-beta.

3. **Prototype `tesserocr` via the existing prebuilt Windows wheel.** A
   much bigger ceiling than the batching item above -- 2.9-6.4x faster
   per call, since it skips subprocess spawning entirely -- but
   genuinely gated on Windows access to verify
   (`docs/ocr-performance-research.md` #1a): `simonflueckiger/
   tesserocr-windows_build` ships prebuilt, self-contained wheels
   (recommended by `tesserocr`'s own README as the Windows install
   path), which drops the risk from "build our own C++ toolchain,
   unproven" to "confirm an existing upstream-endorsed wheel actually
   works for us" -- still unverified, and needs a license check before
   vendoring a third-party compiled binary into something that ships to
   end users. Pair with item 1's Windows beta testing rather than
   treating as separate work.

4. **Guided/interactive calibration.** Calibrating today means running
   `qurio-aug-calibrate`, eyeballing saved crop PNGs in `logs/`, and
   hand-editing fractional boxes in `configs/regions.yaml` until they
   line up. This is the last step in the whole flow that still requires
   real technical comfort. A guided version (step through each region,
   confirm "does this look right?", nudge with arrow-key-style input)
   would remove it.

5. **Chain the first-run flow.** The interactive menu (shipped in
   0.1.2-beta) offers the right options, but a brand-new user still has
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
   investigation faster than it otherwise would be -- several fixes so
   far started from reasoning backward through screenshots to
   reconstruct what the OCR pipeline must have done.

## Low priority

Speculative, reactive, or cost money. Worth having on the list, not
worth prioritizing over the above.

9. **More digit templates.** Only "1" and "2" have the fast pixel-match
   path today, on the assumption (confirmed by the user's play
   experience) that augment deltas are always ±1 or ±2. Only becomes
   necessary if a higher delta is ever actually observed live --
   reactive work, not proactive.

10. **Replace value-text OCR with color + structure analysis.** From
    `docs/ocr-performance-research.md` #3: the delta's color already
    encodes gain (green) vs. gain-at-max-level (orange) vs. loss/None
    (red), and "None" vs. a numeric loss is distinguishable by run
    structure, without needing OCR at all for the common case. Real,
    measured color separation, no new dependency -- but downgraded from
    its original "most promising" ranking now that item 2's batching
    turned out to be a bigger, more validated win for less effort;
    worth revisiting as a complementary optimization after item 2 ships,
    not before.

11. **Self-calibrating OCR thresholds.** `BRIGHT_THRESHOLD`,
    `MIN_MATCH_SCORE`, `INDICATOR_AMBIGUOUS_WIDTH_FRACTION`, and similar
    constants are all tuned against captures from one machine's display.
    A future monitor with different gamma/brightness could need
    different values. No evidence yet that this is actually a problem
    cross-machine -- worth revisiting only if a beta tester's failures
    look threshold-related.

12. **A visible progress readout during a long run** (attempt N/max,
    rough rate, elapsed time) instead of scrolling per-attempt text.

13. **A sound or system notification on accept**, so a long unattended
    run doesn't require watching the terminal to notice it finished.

14. **Code signing.** Removes the Gatekeeper/SmartScreen warnings
    entirely. Costs real money (~$100-400/yr depending on platform) and
    isn't a code change -- a project/budget decision, not engineering
    work.
