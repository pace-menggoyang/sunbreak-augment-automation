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

## 3. Replace value-text OCR with color + structure (most promising)

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

## Recommendation

Priority order if/when this moves from research to implementation:

1. **#3 (color + structure for value-text)** -- most promising, lowest
   risk, builds directly on already-proven techniques in this codebase.
2. **#1 (`tesserocr`)** -- highest ceiling, but prototype the Windows CI
   wheel-build path first as a separate, standalone spike before
   committing to it for real.
3. **#2 (sparkle shape detection)** -- blocked on missing real data; next
   actionable step is capturing a genuine retry-dependent example live,
   not designing a heuristic against samples that don't represent the
   actual problem.
