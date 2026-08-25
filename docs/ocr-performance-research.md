# OCR Performance Research

Findings from investigating further OCR speed improvements, after the
per-row parallelization already shipped (2x measured speedup, see
`ocr.read_page`). Research only -- nothing here is implemented yet. Written
so whoever picks this up next (including a future session) doesn't have to
re-derive it.

## Baseline

A single Tesseract subprocess call averages **218ms** on the dev machine
(measured directly, not estimated). A clean 3-row page needs up to 6 such
calls (name + value-text per row; digits already skip Tesseract via the
fast pixel-template path for "1"/"2"). Parallelizing across rows cut a real
page read from 799ms to 395ms (2x) -- see the `read_page` commit for that
work. The two calls per row that still hit Tesseract unconditionally are
name OCR and value-text OCR.

## 1. `tesserocr` (in-process Tesseract, no subprocess spawn)

**Measured**: 2.9x-6.4x faster per call than the current subprocess
approach (34-104ms vs. 104-218ms depending on crop size), with output
verified identical to the current approach on real data.

**Blocker**: no prebuilt wheel exists on PyPI for Windows, at any Python
version (checked 3.10-3.13 directly against the package index, not
assumed). It's a compiled C extension binding Tesseract's C++ API --
without a wheel, `pip install tesserocr` on Windows needs a full C++ build
toolchain plus Tesseract's dev headers/libs present at install time, which
we have no way to verify works, matching the same "can't test on Windows"
constraint that shaped the original packaging milestone.

**Possible way around it, unverified**: build the wheel ourselves on a
`windows-latest` GitHub Actions runner (Tesseract + Leptonica dev packages
via vcpkg or similar) and vendor the resulting `.whl` into the PyInstaller
build, so end users never need a build toolchain. This is itself an open
research question -- untested, and a meaningfully bigger CI/packaging
change than anything shipped so far.

**Verdict**: highest ceiling of the three options, but not worth pursuing
until the Windows build path is prototyped and confirmed in CI. Don't
implement blind.

### 1a. Update (this session): a prebuilt Windows wheel already exists, unofficially

Re-checked "no wheel exists" against the web, not just PyPI's file listing:
`simonflueckiger/tesserocr-windows_build` (GitHub releases, not PyPI) ships
prebuilt `tesserocr` wheels for Windows -- Python 3.9-3.14, both 32- and
64-bit, self-contained (bundles its own `tesseract.dll`/leptonica, so no
system Tesseract install is required at all). The latest release as of this
check bundles **tesseract 5.5.2**, close enough to the macOS-vendored 5.5.1
that behavior should track closely. This isn't some random fork: `tesserocr`'s
own README points Windows users at this exact repo as the recommended
non-Conda install path.

**Why this changes the calculus**: the CI-wheel-build spike floated above
(vcpkg, `windows-latest` runner, building Tesseract's C++ toolchain
ourselves) may not be necessary at all -- vendoring this repo's existing
`.whl` (extracting its bundled DLLs the same way
`vendor_tesseract_windows.ps1` already vendors Chocolatey's tesseract
install) is a much smaller lift than standing up our own build pipeline.

**Not a green light yet**: this is still desk research, not a Windows test.
Two concrete gaps before trusting it: (1) the repo's README doesn't state a
license for the wheels it distributes -- worth resolving before vendoring a
third-party compiled binary into something that ships to end users; (2)
"self-contained" and "219 stars, referenced by upstream" are good signals
but not the same as confirming it actually imports and runs correctly on the
Windows target this project ships to. Next actionable step: download one
wheel, `pip install` it in a throwaway Windows env (or CI), and confirm
`tesserocr.tesseract_version()` and a real OCR call both work before
committing to this path over #5 or #4a.

### 1b. Update (real Windows hardware, this session): both gaps closed -- it works, and the win is real

Both gaps from 1a resolved with actual Windows access, not desk research:

**License**: the `simonflueckiger/tesserocr-windows_build` repo itself is
MIT-licensed (confirmed on the repo page, not just inferred) -- the
underlying bundled Tesseract (Apache 2.0) and Leptonica (a permissive
BSD-style license) are both already fine to redistribute, same as the
macOS vendoring already does. Resolves 1a's licensing gap for the
wrapper/wheel itself; still worth a final check specifically on
redistribution terms before actually vendoring into a shipped build (a
license existing isn't automatically the same as every clause being
satisfied), but nothing found here blocks moving forward.

**It works.** `pip install`ed the `tesserocr-2.10.0-cp314-cp314-win_amd64.whl`
release (bundling **tesseract 5.5.2**, matching 1a) straight from the
GitHub release URL into this project's real `.venv` on real Windows
hardware -- imports cleanly, `tesserocr.tesseract_version()` reports
`tesseract 5.5.2`, and pointing it at the same `tessdata` directory the
system Tesseract install already uses (`PyTessBaseAPI(path=...)`) picks up
`eng`/`osd` correctly.

**Correctness: byte-identical.** Ran all 6 real name/value crops from
`tests/fixtures/three_rows_reference.png` (the exact crop/bbox/upscale
preprocessing `read_row` produces) through both the current
`pytesseract`-subprocess path and `tesserocr`, same `tessdata`, same PSM
per call -- all 6 outputs matched exactly, including the `"Lv +l"` OCR
noise artifact already documented elsewhere in this doc (confirms this
isn't a cleaner/different result, just a faster path to the identical
one). Also confirmed at the `ocr.read_page()` level: swapping the engine
underneath it still parses the fixture as `['Artillery', 'Diversion',
'Critical Boost']`, identical to today's shipped output.

**Speed: 3.76x, measured against the real production code path, not a
naive baseline.** This is the comparison that matters, and the one #4c's
regression came from getting wrong: `ocr._run_tesseract` was swapped for a
`tesserocr`-backed equivalent (thread-local `PyTessBaseAPI` instances,
since a single instance isn't safe to call concurrently -- the existing
4-worker row thread pool calls it from multiple threads at once), then
`ocr.read_page()` itself -- real, unmodified, already-threaded production
code -- was measured before and after the swap, 20 trials each:

```
current  ocr.read_page() (pytesseract subprocess, 4-worker pool): median 604.8ms
swapped  ocr.read_page() (tesserocr in-process,    4-worker pool): median 161.0ms
speedup: 3.76x
```

Unlike #4c's batching idea, this win doesn't evaporate once measured
against the already-threaded baseline -- it's not competing with the
thread pool for the same source of parallelism (subprocess spawn
overlap), it's removing the subprocess entirely, so the two effects
stack rather than cancel out.

**Not yet validated**: only tested against one clean reference fixture
(`three_rows_reference.png`) -- the messier real captures
(`step-references/*.png`) that validated #4b's batching work aren't
present on this machine (gitignored, and this is a fresh Windows
checkout migrated from macOS mid-session). Worth a pass against those
before fully trusting it, though there's no specific reason to expect
sparkle/border-glow crops to behave differently -- the underlying
Tesseract binary and tessdata are identical either way, only the
subprocess-vs-in-process calling convention changed. Also not yet
integrated into the shipped pipeline -- this was a `.venv`-local
prototype (`pip install` from the GitHub release URL directly, not added
to `requirements.txt`), not wired into `qurio_aug/ocr.py` or
`packaging/vendor_tesseract_windows.ps1`. See `docs/roadmap.md` item 3
for what integration would still need.

## 2. Sparkle-contamination-aware retrying

**Started from**: the user's observation that sparkle-obscured digits
currently just retry-and-wait (fixed 0.25s delay, up to 8x) rather than
actively detecting when a frame is likely to be clean before spending a
retry.

**Investigated**: whether sparkle pixels have a distinguishing color
(near-white/pink, measured from `tests/fixtures/value_crop_*sparkle*.png`)
that could cheaply flag "this looks contaminated" before committing to a
full read.

**Result: ruled out as unsafe in its naive form.** `tests/fixtures/
digit_2_sample.png` -- a real, already-in-production digit template -- is
itself white, not green. A color-based "is this sparkle" filter would
misclassify legitimate white digit content as contaminated, causing
*unnecessary* retries on rows that were actually fine. This would make
things slower, not faster, for a real subset of cases.

**More promising, not yet validated**: visually, the sparkle is a small,
compact, star-shaped glint -- structurally different in *shape* from
glyph strokes, which are more elongated/connected. A shape- or
size-based signal (rather than color) might work, in the same spirit as
`_attempt_debridge`'s existing brightness-threshold approach.

**Missing data, blocking further work here**: the two sparkle fixtures we
have are both cases `_attempt_debridge` already recovers from a single
frame -- they don't represent the actual scenario in question (retries
across *multiple* frames genuinely needed because one frame alone isn't
recoverable). We don't have a saved example of that. **Next time a live
run needs several retries before a sparkle-obscured digit clears, save
that debug capture instead of cleaning it up** -- this research can't
responsibly proceed without a real sample of the target scenario.

**Update (later session): shipped, using exactly that missing data.** A
community member's debug logs + screenshots provided 5 real captures of
this exact scenario. Fixed with a color-based approach after all --
`_attempt_color_debridge` and `_recover_sparkle_contaminated_digit`,
scoped specifically to *green* digit pixels (G channel measurably
outweighing R and B) rather than the naive "any bright non-glyph color is
sparkle" filter ruled out above. That scoping is exactly what sidesteps
the white-digit-template objection: it only ever fires for a confirmed
green gain, never for a white/other-colored digit, so a real white
template being misclassified as contaminated can't happen here. Verified
end-to-end against all 5 real captures; see the project changelog and
`qurio_aug/ocr.py` for details.

## 3. Replace value-text OCR with color + structure -- SHIPPED

While investigating #2, found a better opportunity than originally scoped
(previously floated as "template-match the fixed Lv/+/-/None vocabulary").

**The delta's color already encodes the sign**, measured directly from
real reference screenshots:

| meaning | RGB | R−G / G−B pattern |
|---|---|---|
| gain (normal) | ~(102, 220, 68) | G dominant |
| **gain (skill at max level)** | ~(244, 182, 63) | R dominant, G−B gap ~119 |
| loss / fully removed ("None") | ~(236, 93, 87) | R dominant, G−B gap ~6 |

The maxed-out-gain case (gold/orange, not green) was found live in
`step-references/step-4-augmentation-result.png` row 1 ("Diversion Lv +1")
after the user flagged it from memory -- a naive green-vs-red classifier
would have silently mis-signed every maxed-out skill as a loss. Orange and
red both have R as the dominant channel, so "is R dominant" alone can't
tell them apart, but they separate cleanly on the G−B gap (~119 vs. ~6, a
wide margin). Green vs. non-green is a simpler, already-safe split (G
dominant vs. not).

**"None" vs. a numeric loss**: both render in the same red, so color alone
can't distinguish them, but "None" splits into 4 letter-shaped column-runs
(~18-24px each, confirmed via `_column_runs` against a real reference)
where a numeric value produces 1-2 runs including one narrow (~10px)
digit run. That structural difference is already the same kind of signal
`select_digit_run`/`indicator_region_ambiguous` rely on elsewhere in this
codebase.

**Why this is the strongest candidate**: it could eliminate the
value-text Tesseract call entirely for the common case (color for
sign/max-state, the already-working digit-template path for the number,
run-structure to catch "None"), falling back to OCR only when genuinely
ambiguous. No new dependency, no Windows packaging risk (pure pixel
analysis, same technique already proven for `indicator_region_ambiguous`
and `is_augmentation_results_screen`), and only one real orange sample
observed so far -- worth staying open to more color variety once this is
actually built and exercised against live data, but the separation is
wide enough to be a reasonable base to design against.

**Shipped** (`_classify_value_fast` in `qurio_aug/ocr.py`): thresholds
validated against every real row in `tests/fixtures/three_rows_reference.png`
-- a normal gain, the same real maxed-out "Diversion" gain this research
found live, and a real "None" removal -- plus 7 synthetic boundary tests
pinning down each dead zone precisely (`tests/test_ocr.py`). "None" vs. a
numeric loss ended up resolved by the *first* column-run's width, not run
count as originally floated here: a real "None" fixture's own *last* run
measured 18px, under the 20px `MAX_DIGIT_RUN_WIDTH` a naive reuse would
have checked, which would have misclassified it as numeric. The first
run's width (~34-38px "Lv" prefix vs. ~24px "None"'s first letter)
separates cleanly instead, with margin confirmed on both sides.

Falls back to the original Tesseract read whenever color or structure
lands in a dead zone (measured live: one of four sparkle-contaminated
fixtures does, correctly). Measured on the real, already-threaded
`ocr.read_page()` production path: **1.47x faster** with `tesserocr`
active (Windows default), **1.76x faster** on the pure
pytesseract-subprocess path (macOS, or `tesserocr` unavailable) -- the
win is larger without `tesserocr` since the avoided call cost more to
begin with. All 155 tests pass, zero changes needed to any pre-existing
test.

## 4. Batch multiple Tesseract calls into one subprocess invocation

**Started from**: isolating where the ~100ms-per-call cost actually goes.
`tesseract --version` (bare process spawn, no OCR) measures **7.4ms**
median; `pytesseract.image_to_string` on a blank 10x10 image (near-zero
image-analysis work, but the full pytesseract path: temp file write,
process spawn, **traineddata load**, output file read) measures **58ms**
median. A real name/value crop measures 90-106ms. So roughly half of every
call's cost is fixed overhead (dominated by loading `eng.traineddata` into
the LSTM engine fresh, every single call) that has nothing to do with the
image content -- meaning it should amortize if multiple crops are OCR'd in
one process invocation instead of one each.

**Measured**: stacking all 6 of a page's name/value crops (3 rows,
already-preprocessed: bbox-cropped + 5x upscaled, exactly as `read_row`
produces them today) into one tall composite image with black gaps
between them, then a single `--psm 6` call, reads all 6 correctly and
takes **350ms** vs. **626ms** for the current 6 separate `--psm 7` calls
-- **1.79x faster**, comparable in size to the per-row parallelization win
already shipped. This is on top of (not instead of) that parallelization
-- batching cuts the number of Tesseract calls a page needs; the thread
pool still applies to whatever's left (digit fallback OCR, per-row work
that can't be pre-composited).

**Correctness pitfall, found and fixed during this research**: naively
splitting the composite's output on newlines is unsafe. Real pages
regularly have a blank value cell (e.g. an unaffected slot), and
`_ocr_content` (ocr.py:318-328) already skips the Tesseract call entirely
for a blank crop via `_bright_bbox` returning `None` -- but a blank
*slot inside a composite* doesn't reliably produce a blank *line* in
Tesseract's output; it can just disappear, shifting every line after it
up by one and silently misattributing text to the wrong row/field.
Reproduced directly: batching 5 non-blank crops (skipping one blank value
slot) via plain `image_to_string` + newline-split yields exactly 5 lines
with no signal of *which* slot went missing.

**Fix, verified working**: use `pytesseract.image_to_data(...,
output_type=Output.DICT)` instead of `image_to_string` -- same call, same
cost (106ms vs. 104ms measured, negligible difference) -- which returns
each detected line's pixel `top`/`height` alongside its text. Since the
composite is built by this code (every crop's paste-y-offset is known),
each detected line's vertical center can be matched back to the nearest
known slot instead of relying on line order. Verified against a
6-slot composite with one slot deliberately left blank: all 5 present
crops matched to their correct row+field, and the blank slot correctly
came back absent rather than misaligning anything downstream.

**Caveats / what's not yet validated**: only tested against one
reference page (`three_rows_reference.png`, 3 clean rows) with one
synthetically-blanked slot -- not yet run against the wider fixture set
or against messier live captures (sparkle contamination, border glow,
multi-page continuation layout). `--psm 6` (block) vs. the current
per-crop `--psm 7` (single line) reads the same text but isn't
byte-identical -- one run showed `"Lv +"` (psm 7) vs. `"Lv +i"` (psm 6)
for the same crop, an extra noise character that doesn't change the
parsed sign but hasn't been checked against `parse_value`'s regex across
the full existing test suite. A 20px black gap between stacked crops was
enough to keep Tesseract from merging adjacent blocks into one paragraph
in this test, but that's one data point, not a proven-safe margin.

**Verdict**: the strongest lead found so far -- bigger and more certain a
win than #3, no new dependency, no Windows packaging risk (pure
pytesseract API usage already in the codebase), and directly explains
*why* it works (measured fixed per-call overhead) rather than just
observing a speedup. Needs validation against the full fixture set and a
few messier live captures before implementing, and the position-matching
logic (not naive line-splitting) is load-bearing, not optional.

### 4a. Refinement (this session): tesseract's native `imagelist` mode supersedes the composite image

While validating #4 against real fixtures (`tests/fixtures/three_rows_reference.png`,
via the exact crop/bbox/upscale path `read_row` uses, `_THREE_ROWS_CONFIG` from
`tests/test_ocr.py`), found a better mechanism for the same idea: tesseract's CLI
natively accepts a *list* of image files in one invocation --
`tesseract imagename|imagelist|stdin outputbase` (confirmed via `tesseract
--help-extra`, tesseract 5.5.1) -- where `imagelist` is a text file, one image path
per line. Each listed image is treated as a "page" of one logical document and OCR'd
with the same loaded engine, separated in the output by a form-feed (`\f`) byte, one
segment per input image, always, in order -- confirmed directly, including for an
empty/blank page (a `\f\f` with nothing between).

**Why this is better than the composite image:**
- **No position-matching needed.** Since the batch list is built by this code, one
  entry per non-blank crop (blank crops are simply *not added* to the list, exactly
  matching how `_ocr_content`'s `_bright_bbox` check already skips them today), the
  output's `\f`-delimited segments align 1:1 with the input list by construction.
  Verified directly: fed a batch with row 1's value crop deliberately excluded (3
  names + 2 values, 5 entries) -- got back exactly 5 `\f`-delimited segments, no
  ambiguity, no `image_to_data` bbox-matching machinery required at all.
- **No psm mismatch.** Each listed image is OCR'd as its own page with the *same*
  `--psm 7` used today (not forced into `--psm 6` like the composite approach
  needed to keep blocks from merging) -- verified byte-identical output to the
  current per-crop calls on all 6 real crops from `three_rows_reference.png`,
  including the existing `"Lv +l"` noise artifact (present in both, so batching
  introduces no new noise).
- **Simpler.** No composite-image construction, no gap-sizing tuning, no risk of
  Tesseract merging adjacent blocks.

**Not free of caveats, though: measured against the wrong baseline.** Raw
subprocess-only timing looks dramatic -- 6 sequential `pytesseract.image_to_string`
calls averaged **610ms**, vs. **283ms** for one `imagelist` call over the same 6
crops (2.16x) -- but that compares against an *unthreaded* baseline. The codebase
already ships the 4-worker thread pool from the per-row parallelization work
(`_get_row_executor`), and re-measuring `ocr.read_page` itself (not a hand-rolled
sequential loop) on the same fixture gives **403-432ms** across repeated trials --
much of the naive per-call overhead is already hidden behind concurrency, since 3
rows' worth of calls (name+value each) run across the pool at once. Redoing the
`imagelist` measurement honestly -- including the temp-file writes every real call
needs, not just the subprocess invocation -- gives **~363ms** (reusing one temp
directory instead of creating/destroying one per call made no measurable
difference). So the real, apples-to-apples win over what's *actually shipped* is
**~1.1-1.2x**, not the ~1.79x/2x a naive sequential comparison implies. Still a real
win, and it collapses 3 concurrent tesseract subprocess spawns into 1, which may
matter more under process-spawn contention on weaker hardware (the Windows target
this ships to) than measured here on the dev Mac -- but that's untested, flagged
as a hypothesis, not a result.

**Implementation note**: `pytesseract` has no built-in support for the `imagelist`
form (its API is one-PIL-image-per-call by design, via `run_tesseract`/
`image_to_string` in `pytesseract/pytesseract.py`) -- this path needs a raw
`subprocess.run(["tesseract", listfile, out_base, "--psm", "7", "txt"])` call for
just the batched name/value step, bypassing pytesseract there, with the same
resilience wrapping `_run_tesseract` already does for the individual calls
(missing-output-file / TesseractError -> treat as empty, don't crash the run).
Digit OCR stays untouched (separate config, whitelist, multi-psm retry loop --
not batchable the same way) and keeps running through the existing thread pool.

**Still needs, before implementing**: validation against the full fixture set
(sparkle/border-glow crops, multi-page continuation layout) and a few messier live
captures, same as #4's original caveat -- this session only re-confirmed
correctness and re-measured timing on the one clean reference fixture.

### 4b. Further validated (this session): border glow, continuation layout, and a real blank slot

Went back and closed most of 4a's "still needs" gap using real captures already
present locally (`step-references/*.png`, gitignored but not absent -- these are
the same screenshots referenced elsewhere in this doc for the maxed-out-gain
color sample) rather than the one synthetic reference fixture:

- **`step-4-augmentation-result.png`** (single-page layout, the same capture
  cited in #3 for its border-glow-adjacent row) -- batched all 6 name/value
  crops via `imagelist`, compared against individual per-crop calls: all 6
  byte-identical, including the row with the animated border-glow bleed
  discussed in `select_digit_run`'s docstring.
- **`step-7-page2-result.png`** (`continuation` layout, page 2 of a multi-page
  roll) -- this page has only 1 of 3 row slots filled; the other 2 rows'
  name *and* value crops are genuinely blank (not synthetically blanked like
  the earlier test). Batch correctly included only the 2 non-blank crops (the
  filled row's name + value) and excluded all 4 blank ones, matching
  `_ocr_content`'s existing `_bright_bbox` skip exactly -- no special-casing
  needed, this falls out of "only add non-blank crops to the imagelist" by
  construction.
- **`multipage-result-page1.png`** (`first_of_multi` layout) -- all 6 crops
  present and byte-identical between individual and batched calls, including
  the same `"Lv -|"` stray-character noise pattern seen elsewhere in this doc
  (confirms the noise is a property of upscaled-bitmap-font OCR in general,
  not specific to one fixture).

All three of #4a's flagged gaps (border glow, continuation layout, a *real*
blank slot rather than a synthetically-blanked one) are now covered with
real captures, not just the one clean reference fixture. What's still not
covered: heavy live variability across many different rolls/sessions --
this is still "validated against a handful of real screenshots," not
"battle-tested against hundreds of live reads."

### 4c. Implemented, measured, and reverted (this session): a real regression, not a win

Built it -- `_run_tesseract_batch` (raw `subprocess.run` with tesseract's
`imagelist` mode, bypassing pytesseract for just this path, reusing its
configured `tesseract_cmd`), `_prepare_content_crop` split out of
`_ocr_content` so `read_page` could batch prep across a whole page before
one call, `_finish_row` split out of `read_row` so the non-batchable
digit-extraction remainder still ran on the thread pool. Correctness held
up completely: byte-identical `ParsedRow` output against all three real
captures from #4b (clean, border-glow, continuation-with-a-real-blank-slot)
plus the 5 real sparkle-contamination captures from #2's actual fix (see
below) -- all 61 project tests passed.

**Then measured it head-to-head against what's actually shipped, and it's
slower, not faster:**

```
old (existing 4-worker thread pool, one tesseract call per crop): 339ms median
new (batched into one tesseract call via imagelist):              476ms median
```

10 interleaved trials on the same real page (`step-4-augmentation-result.png`,
6 crops), low variance on both sides (old: 332-391ms, new: 473-477ms) --
not noise, a consistent ~1.4x regression.

**Why the earlier estimate was wrong**: the ~1.1-1.2x win in #4a/#4b was
computed by comparing one batched call's wall-clock against the existing
threaded implementation's wall-clock, but never isolated *why* the
threaded version was already fast -- it was measured as a black box. The
theory (amortizing tesseract's ~50ms fixed per-call startup cost, mostly
`eng.traineddata` loading) is real, but it only pays off if that fixed
cost is actually being paid serially. On this dev machine (8 cores, 4
performance + 4 efficiency), the existing thread pool runs 4 tesseract
subprocesses concurrently -- each one's ~50ms startup cost overlaps with
the others' almost entirely, so there's very little serialized fixed
overhead left to amortize away. Collapsing 6 calls into 1 sequential
process trades that already-cheap parallelism for a single-threaded run
that has to process all 6 images one after another inside tesseract
itself, with nothing else to overlap it against -- a net loss once
parallelism is already doing the job the batching was meant to do.

**Not necessarily wrong on different hardware.** The original doc flagged,
as an untested hypothesis, that this might matter more "under
process-spawn contention on weaker hardware (the Windows target this ships
to)" -- a machine with fewer cores than the thread pool's 4 workers would
see less overlap from threading in the first place, changing this
trade-off's direction. Still completely unverified; no Windows access to
check it from here. If a low-core-count Windows report ever surfaces real
per-attempt timing that looks parallelism-starved, this is worth
revisiting with real data from that machine -- but "try it on faster
hardware and hope" isn't a plan, and this doc's job is to stop the next
pass from re-implementing this blind based on the old (wrong, black-box)
estimate.

**Verdict: dead end on this hardware, reverted, not shipped.** The
implementation is correct and battle-tested against real captures, but
correctness was never the question -- speed was, and the honest, measured
answer on the hardware available here is that it's a regression.

## 5. Tessdata model variant (fast vs. best) -- checked, no action needed on either platform

While isolating call overhead, checked which `eng.traineddata` variant is
actually in use, since swapping "best" (accurate, slow float model) for
"fast" (integer model, meaningfully smaller/quicker) is a classic
Tesseract speed lever that costs nothing but a file swap.

**Finding**: Homebrew's `tesseract` formula (what the macOS vendoring
script in `packaging/vendor_tesseract_macos.sh` copies from) already
pulls `eng.traineddata` from `tesseract-ocr/tessdata_fast`, confirmed
straight from the formula source
(`brew cat tesseract` shows the resource URL). So this lever is already
pulled on macOS -- no action there. Confirmed indirectly too: `--oem 0`
(legacy engine) fails outright against this file with "components are not
present," which is exactly what a `tessdata_fast`-only (LSTM-only) file
does.

**Update (real Windows hardware, this session): also already the fast
variant.** Checked the same way: the Windows path vendors from a
Chocolatey install (`packaging/vendor_tesseract_windows.ps1`, UB-Mannheim's
tesseract build) -- its vendored `eng.traineddata` is 4.1MB (matching
`tessdata_fast`'s known small size, vs. `best`'s much larger float model),
and `--oem 0` against it fails with the identical "components are not
present" error macOS's file produces. No swap needed on Windows either --
both platforms were already pulling the fast variant this whole time.

## 6. Ruled out / checked with no meaningful win (this session)

Four smaller ideas investigated and closed out, recorded so a future session
doesn't re-derive them from scratch:

- **Avoiding temp-file I/O via stdin/stdout piping.** Tesseract's CLI accepts
  `stdin`/`stdout` as pseudo-filenames, which would skip the temp-file
  write+read `pytesseract.image_to_string` does on every call. Measured
  directly against the same 6 real crops used elsewhere in this doc: temp-file
  (current) averaged 595ms for all 6, stdin/stdout piping averaged 640ms --
  *slower*, not faster, and not a "verified negligible" case, a real
  regression. Confirms this doc's own #4 finding: the ~100ms/call cost is
  dominated by loading `eng.traineddata` fresh per process, not file I/O, so
  removing the file I/O has nothing to save.
- **Growing the thread pool past 4 workers.** With `_get_row_executor`'s pool
  size bumped to 6, 8, and 12 (dev machine has 8 cores), `read_page` on the
  3-row reference fixture stayed flat at ~393-397ms across all of them vs.
  ~393ms at 4 -- no measurable difference. Expected in hindsight: a 3-row page
  is only 6 calls total, and 4 already lets 4 run concurrently, so there's
  barely any queuing left to remove. Not a lever at current page sizes.
- **Disabling Tesseract's dictionary correction** (`-c load_system_dawg=0 -c
  load_freq_dawg=0`) for name OCR, on the theory that skill names are proper
  nouns `rapidfuzz`-matched against a known vocabulary afterward anyway, so
  Tesseract's English-dictionary "correction" is pure overhead (and a
  latent accuracy risk -- it could nudge an unusual name toward a real
  English word). Measured: 122ms vs. 117ms per call on the 3 name crops from
  `three_rows_reference.png` -- within noise, not a real win -- and output was
  byte-identical on all 3, so no accuracy signal either way from this sample.
  Not worth pursuing on its own; if #4a/#4b ships, this could still be added
  to the batched call for free (no measured downside), just don't expect it
  to move the needle.
- **OpenMP thread contention across concurrent Tesseract subprocesses**
  (a real, documented issue in older Tesseract versions -- 4 OpenMP threads
  per process times N concurrent processes over-subscribes the CPU, and
  setting `OMP_THREAD_LIMIT=1` is the standard fix). Doesn't apply here:
  Tesseract removed OpenMP entirely as of the 5.0 development line (2019),
  and both the macOS-vendored version (5.5.1) and the Windows Chocolatey/
  UB-Mannheim builds (5.x) postdate that by years -- confirmed on macOS
  directly too (`otool -L` on the vendored `tesseract` binary shows no
  `libomp`/OpenMP linkage at all). Nothing to tune here.

## Recommendation

Priority order if/when this moves from research to implementation. #1b and
#2 are shipped and #4a/#4b is a closed dead end -- all three kept below
with their outcomes rather than deleted, so none gets re-investigated
blind.

1. **#1b (`tesserocr` via the existing prebuilt Windows wheel) --
   shipped.** Both gaps #1a flagged closed (MIT-licensed; confirmed
   working, byte-identical output on every real crop tested), then
   integrated end-to-end: `requirements.txt` (a version- and
   platform-marker-gated direct wheel URL, since it isn't on PyPI),
   `_run_tesseract` now prefers a thread-local `tesserocr.PyTessBaseAPI`
   per row-thread-pool worker (falling back to the pytesseract subprocess
   path cleanly if tesserocr is unavailable or its tessdata can't be
   resolved -- see `tesseract_setup.tesserocr_tessdata_dir`), and
   `--selfcheck` now reports whether the accelerator is active. **3.76x
   speedup measured against the real, already-threaded `ocr.read_page()`
   production path**, not a naive/wrong baseline (unlike #4a/#4b). The
   compiled build needed one packaging fix beyond source-level
   integration: `tesserocr.cysignals` (a nested compiled submodule) isn't
   traced by PyInstaller's static import analysis and has to be listed
   explicitly in `packaging/qurio-aug.spec`'s `HIDDEN_IMPORTS` -- confirmed
   live: omitting it silently produced a working exe whose accelerator was
   inactive (`ModuleNotFoundError`), not a build failure, so this is easy
   to miss without actually running the compiled output. No changes needed
   to `packaging/vendor_tesseract_windows.ps1` -- tesserocr's own DLLs live
   inside its package directory and get auto-bundled by PyInstaller, and
   it reuses the tessdata already vendored there for the subprocess path.
   Still not validated against messier real captures
   (`step-references/*.png`, not present on the machine this was built
   on) -- worth a pass if a live report ever suggests it behaves
   differently from the subprocess path, though there's no specific
   reason to expect that (same Tesseract binary and tessdata either way).
2. **#5 (confirm Windows tessdata variant) -- checked, no action needed.**
   The vendored Windows `eng.traineddata` is already `tessdata_fast`
   (confirmed by file size + the same `--oem 0` failure signature used to
   confirm macOS), same as macOS. Also now doubly moot with #1b shipped,
   since tesserocr points at that same vendored tessdata too.
3. **#3 (color + structure for value-text)** -- still promising and still
   builds on already-proven techniques (the same green-channel-dominance
   check #2's fix below uses, just applied to more of the value read
   instead of only sparkle recovery).
4. **#4a/#4b (batch calls via tesseract's native `imagelist` mode) --
   implemented, measured, reverted; see #4c.** Correct, but ~1.4x
   *slower* than the already-shipped 4-worker thread pool on the 8-core
   dev machine this was measured on: the thread pool already overlaps
   most of tesseract's per-call startup cost across its 4 concurrent
   subprocesses, leaving little serialized fixed overhead left to
   amortize by batching into one process. The original estimate was a
   black-box wall-clock comparison that never isolated why the threaded
   baseline was already fast -- this is why. Not necessarily wrong on a
   lower-core-count machine (the untested hypothesis that motivated
   trying this in the first place), but "try it on different hardware
   and hope" isn't a plan -- only worth revisiting with real timing data
   from a machine that's actually parallelism-starved, not blind.
5. **#2 (sparkle-contamination-aware retrying) -- shipped.** A community
   member's debug logs + screenshots supplied exactly the missing real
   data this was blocked on. Fixed with a green-channel-dominance color
   check scoped specifically to gains (side-stepping the white-digit-
   template objection that ruled out a naive version of this idea
   above), covering both a sparkle bridging the sign to the digit and one
   sitting directly on the digit itself. See `qurio_aug/ocr.py`
   (`_attempt_color_debridge`, `_recover_sparkle_contaminated_digit`) and
   the changelog.
