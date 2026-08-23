# Changelog

All notable changes to this project are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/); this
project doesn't yet follow strict semantic versioning (it's still beta).

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
