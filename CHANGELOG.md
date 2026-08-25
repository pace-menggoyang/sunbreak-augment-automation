# Changelog

All notable changes to this project are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/); this
project doesn't yet follow strict semantic versioning (it's still beta).

## [Unreleased]

- Feature: a long run now shows a live, in-place progress readout
  (attempt N/max, rough attempts/min rate, elapsed time) instead of a
  full decision line scrolling past for every single attempt -- almost
  all of which are uninteresting rejects in a long run, and the full
  detail is always in the `.log`/`.jsonl` files regardless. An accepted
  attempt, or one flagged suspicious (a possible OCR misread -- an
  unusually large delta), still prints its full line immediately, since
  those are exactly the moments worth seeing live. Only kicks in when
  connected to a real terminal; output redirected to a file or pipe
  prints every attempt's full line unchanged, since an in-place update
  would just be unreadable noise there.
- Perf: the value cell's "Lv +", "Lv -", or "None" text is now read via
  color + structure pixel analysis instead of a Tesseract call, for the
  common case -- the delta's rendered color already encodes gain (green)
  vs. gain-at-max-level (orange) vs. loss-or-removed (red), and red's
  remaining ambiguity ("None" vs. a numeric loss both render identically)
  resolves via the first text run's width instead ("Lv" prefix vs.
  "None"'s narrower first letter). Falls back to the original Tesseract
  read whenever color or structure lands in a dead zone -- confirmed live
  on one of four real sparkle-contaminated fixtures. Measured on the real
  `ocr.read_page()` production path: **1.47x faster** with `tesserocr`
  active (Windows default), **1.76x faster** on the pure
  pytesseract-subprocess path (macOS, or wherever `tesserocr` isn't
  available) -- the win is larger without `tesserocr` since the avoided
  call cost more to begin with. See `docs/ocr-performance-research.md`
  #3 for the full measurement writeup, including a real pitfall caught
  before shipping: naively reusing an existing digit-run-width check
  would have misclassified a real "None" removal as numeric.
- Perf (Windows): the compiled `.exe` shrank from 149MB to **43.8MB**
  (71%) in two steps, both in `packaging/trim_tesseract_windows.py`:
  - Trimming the vendored Tesseract install to exactly what
    `tesseract.exe` needs to run, instead of copying Chocolatey's entire
    install directory. Computed via a real PE import-table walk (same
    idea as `vendor_tesseract_macos.sh`'s `dylibbundler` step, just for
    PE instead of Mach-O) -- previously not possible to verify without
    real Windows hardware, which is exactly why the blind
    copy-everything approach was chosen in the first place. Of the
    ~229MB raw install, ~110MB was dead weight: a dozen model-training
    tools this project never invokes (`text2image.exe`,
    `lstmtraining.exe`, `mftraining.exe`, etc.) plus a large
    ICU/Pango/Cairo stack only *those* tools need, not OCR itself. Took
    the exe from 149MB to 94.4MB (37%) on its own.
  - Discovered next, while looking into shrinking it further: both the
    official UB-Mannheim Tesseract build and Chocolatey's repackaging of
    it (confirmed byte-identical) ship `libtesseract-5.dll` as an
    **unstripped mingw debug build** -- 97MB of its 101MB is orphaned
    DWARF debug sections and a COFF symbol table, confirmed via a real
    PE section dump and all flagged `IMAGE_SCN_MEM_DISCARDABLE` (the
    Windows loader never keeps them resident even in the original, so
    removing them changes nothing at runtime). No `strip`/`objcopy`
    exists on this Windows toolchain by default, so
    `trim_tesseract_windows.py` now does the equivalent itself: truncate
    the file at the debug sections' offset and fix up the handful of PE
    header fields that reference them. Took the trimmed dependency
    closure from 121MB to 21.7MB. Verified byte-identical OCR output
    against the original, unstripped binary across 5 real fixtures x 4
    psm modes (32 comparisons) before trusting it, plus a full
    `ocr.read_page()` run through the stripped binary matching the
    project's own known-good expected result exactly.

  Verified end-to-end after both steps: `tesseract.exe --version`, real
  OCR calls through the trimmed+stripped subprocess path, and the full
  debridge/sparkle-recovery pipeline against a real contaminated
  fixture all still work correctly; the compiled exe's `--selfcheck`
  passes with both the subprocess and `tesserocr` paths active.

## [0.1.4-beta] -- 2026-08-23

First real Windows hardware + live game testing since v0.1.0-beta's
CI-only verification (see `docs/windows-beta-checklist.md`) -- three real
bugs found and fixed below; calibration, single- and multi-page OCR,
pagination, and the decision engine all confirmed working correctly
against the live game at native 1920x1080. Two real (non-dry-run) farming
runs also confirmed every macro this tool sends actually lands on real
Windows hardware: a `--max-attempts 5` run against a real goal confirmed
the reject/reroll path end-to-end (including a correct real
protected-skill-violation rejection), and a second run confirmed the
accept/confirm macro, accepting and applying a real roll on its first
attempt.

- Fix: `--list-windows` crashing outright (`UnicodeEncodeError`) the
  moment any visible window's title contains a character outside the
  console's legacy codepage -- not an edge case in practice (hit
  immediately from a styled Discord server name and VS Code's own "..."
  title truncation). Reconfigure stdout/stderr with `errors="replace"`
  on Windows so an unencodable character prints as "?" instead of
  crashing the one diagnostic command meant to help find the right
  `--window` value.
- Fix: window owner-process matching silently never working on Windows.
  `_owner_name()` called `win32process.OpenProcess`, which doesn't
  exist -- the function lives on `win32api`, not `win32process`. Every
  call raised `AttributeError`, swallowed by a broad `except Exception:
  return ""`, degrading every match to title-only with no error or
  indication anything was wrong. Never caught before now because
  `--selfcheck` (the only thing CI runs against a real Windows machine)
  never exercises this path. Confirmed live: every window's owner now
  correctly resolves (e.g. `MonsterHunterRise.exe`) instead of `''`.
- Fix: the default start/stop hotkeys (Control+M / Control+N) collide
  with Monster Hunter Rise: Sunbreak's own default Windows control
  scheme -- bare Control is bound to "Skill Info" and bare Shift to
  "Compare Equipment" on the Augmentation Results screen itself (visible
  in its own on-screen button-hint bar). Since pynput's global hotkey
  listener observes keystrokes without suppressing them (the same
  mechanism already documented here for an unrelated macOS collision),
  the physical Control keydown reaches the focused game regardless of
  which letter follows it, popping the Skill Info overlay over the exact
  screen this tool is about to read. Confirmed live: a dry-run's capture
  came back with the Skill Info panel visibly covering the results,
  reading as a fully blank roll. Windows now defaults to Alt+M/Alt+N
  instead (not bound to anything in that hint bar); macOS keeps
  Control+M/Control+N, which is already proven collision-free there.
  Verified end-to-end after the fix: a full multi-page roll (page 1 +
  page 2 via a real Q keypress) read and decided correctly with no
  interference.
- Feature: edit an existing goal from the interactive menu (option 2),
  not just build new ones with the wizard. Tweaking a required skill's
  minimum level, adding/removing an allowed additional skill, editing
  protected skills, adding/removing a whole profile, or renaming the
  goal/augment type no longer requires hand-editing YAML -- reuses the
  wizard's own skill-name resolution (typo correction included) and
  validation. Verified end-to-end against a real user goal config
  (`gila-minmax.yaml`, 4 profiles): edited a required skill's level,
  saved, and reloaded the file to confirm the change round-tripped
  correctly with the rest of the goal untouched.
- Perf (Windows): OCR now runs through `tesserocr` (in-process) instead of
  spawning a `tesseract` subprocess per call, when available -- **3.76x
  faster**, measured against the real, already-threaded `ocr.read_page()`
  production path (not a naive baseline), with byte-identical output on
  every crop tested. Installs automatically via `requirements.txt` from a
  prebuilt wheel (`simonflueckiger/tesserocr-windows_build`, MIT-licensed;
  not on PyPI, so pinned to a specific direct URL, Windows + this
  project's standard Python version only) and falls back to the existing
  subprocess path cleanly if it's ever unavailable -- macOS is completely
  unaffected. `--selfcheck` now reports whether the accelerator is
  active. See `docs/ocr-performance-research.md` #1b for the full
  measurement writeup, including a packaging gap
  (`tesserocr.cysignals` needing an explicit PyInstaller hidden-import)
  found and fixed by actually running the compiled build, not just the
  source checkout.
- Feature: `--package-failure` (also in the interactive menu, option 8)
  bundles the most recent debug log (+ its matching `.log`/`.jsonl`) and
  the most recent saved failure screenshots from `logs/` into one zip,
  ready to attach to a bug report -- no more hunting through `logs/` by
  hand to figure out which files are actually relevant. The two are found
  independently (newest debug log, newest `unreadable_*` screenshot
  cluster) rather than assumed to be from the same run, since the most
  recent run might have succeeded while an earlier one failed.
- Feature: confidence tagging in the debug log -- each row's entry now
  records *how* its digit was actually read (`template`, a specific
  `tesseract:psmN`, or last-resort `sparkle-recovery`) and whether a
  merged run needed debridging first (`debridged:color` or
  `debridged:brightness`), e.g. `Artillery +1 [template]` or `Diversion
  +1 [tesseract:psm8, debridged:color]`. Previously only *what* a row
  parsed as was recorded; several earlier fixes in this project started
  from reasoning backward through screenshots to reconstruct what the
  OCR pipeline must have done to produce a given misread -- this is
  exactly the information that took. Found immediately useful on its own
  first real test: the "clean" reference fixture used throughout this
  project's test suite turns out to need color-debridging on one of its
  two rows, previously invisible.

## [0.1.3-beta] -- 2026-08-23

- Feature: the interactive menu's "start farming" option now prompts for
  max attempts (blank keeps the default of 300) instead of always using
  the default with no way to override it short of the CLI's
  `--max-attempts` flag. Dry-run isn't asked, since it ignores
  max_attempts entirely.
- Fix: recover a digit obscured by a bright "newly changed" sparkle that
  never fades, instead of leaving the row permanently unreadable.
  Reported by a community member (debug logs + screenshots of a "Lv +1"
  gain staying unparseable across all 8 retries, forcing a manual
  restart -- confirmed 5 times across ~1,558 logged attempts). The
  existing sparkle recovery only works when the sparkle is dimmer than
  the digit; these captures all show one just as bright, which no
  brightness threshold can separate from the digit itself. Fixed with a
  color-based check instead (a real digit stroke's green channel
  measurably outweighs the sparkle's), covering both a sparkle bridging
  the sign to the digit and one sitting directly on top of it. Verified
  against all 5 real captures end-to-end -- all now correctly recover
  "+1".
- Fix: stop leaking the full pixel buffer of every screenshot on macOS.
  Converting the captured frame's pixel data with Python's `bytes()`
  constructor leaked the entire buffer on every single call -- a PyObjC
  bridging quirk, not a logic bug -- accounting for multiple GB over a
  long run (a community member's Force Quit dialog showed the process
  climbing past 6GB). Switching that one conversion to `bytearray()`
  eliminated it entirely; verified with a 60-capture loop that went
  completely flat after the fix versus climbing by roughly 1GB before it.
- Fix: stop leaking one cache slot per OCR call. `pytesseract` deletes
  its own temp files after every call via a glob pattern that's never
  the same twice, so Python's `fnmatch` pattern-compile cache grew by
  one permanently-useless entry per call. Bounded (tens of MB at worst)
  rather than a major contributor on its own, but real, measured growth
  for a cache that never once hit -- now cleared after every call.

## [0.1.2-beta] -- 2026-08-19

- Feature: an interactive numbered menu (wizard, calibrate, dry-run, start
  farming, selfcheck, list windows) when the tool is run with no arguments
  -- previously double-clicking the compiled exe just flashed an error and
  closed, confusing for non-technical users with no reason to expect a
  command line. Any explicit flag still goes through the normal CLI path
  unchanged.
- Fix: detect when the game never actually landed on the Augmentation
  Results screen (e.g. a leftover "requires materials" confirmation
  dialog) instead of trying to OCR skill rows off the wrong screen and
  failing with a generic, unhelpful error. Only checked once an existing
  read has already exhausted its retries and is about to fail anyway, so
  there's no added cost on a normal successful attempt.
- Perf: read a page's 3 skill rows concurrently instead of sequentially
  (~2x faster per page read end-to-end), since each row's OCR is
  independent and I/O-bound. Also fixes a PIL thread-safety issue this
  surfaced during testing.
- Perf: reuse one mss.mss() screen-capture instance for the whole run
  instead of creating and tearing one down on every single screenshot,
  which is unnecessary overhead on Windows and a plausible factor in a
  community report that the loop runs but "something's off."

## [0.1.1-beta] -- 2026-08-18

OCR/pagination robustness fixes since v0.1.0-beta.

- Fix: verify the page-turn indicator actually advanced before trusting
  a page 2+ read, re-checked on every retry (not just once up front).
- Fix: always navigate back to page 1 when a page 2+ read fails, not
  just on success -- prevents one crash's leftover game state from
  corrupting the next run's first read.
- Fix: retry the initial page-indicator read when it looks corrupted
  (real content that failed to parse) rather than genuinely missing --
  fixes a live bug where a red-border-glow-corrupted indicator
  permanently locked an attempt into the wrong row template.
- Fix: don't crash the whole run on a transient tesseract subprocess
  hiccup (a missing temp output file) -- treated as a retryable empty
  read instead of a hard process exit.
- Feature: step-by-step debug trace written to
  `logs/<goal>-<timestamp>.debug.log` for every attempt, not just
  terminal output that vanishes with the window.

## [0.1.0-beta] -- 2026-08-17

First public beta -- CI-verified on macOS and Windows; Windows path not
yet tested against real hardware/game, see
[docs/windows-beta-checklist.md](docs/windows-beta-checklist.md).
