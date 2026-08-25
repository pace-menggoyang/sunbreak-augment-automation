# Sunbreak Qurio Augmentation Automation

[![build](https://github.com/pace-menggoyang/sunbreak-augment-automation/actions/workflows/build.yml/badge.svg)](https://github.com/pace-menggoyang/sunbreak-augment-automation/actions/workflows/build.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![platform](https://img.shields.io/badge/platform-macOS%20%7C%20Windows-lightgrey)

Reads the Qurio armor augmentation result screen in **Monster Hunter Rise: Sunbreak**,
decides whether a roll matches a goal you define, and either applies it or
rerolls -- looping until it finds a match, since outcomes are seeded and
rerolling is the only lever. No memory editing, no game files touched:
it reads the screen and sends the same key presses you would.

## Contents

- [Features](#features)
- [Quick Start (download and run)](#quick-start-download-and-run)
- [Setup (from source / development)](#setup-from-source--development)
- [Calibration](#calibration-do-this-first-against-the-live-game)
- [Define a goal](#define-a-goal)
- [Validate before trusting it unattended](#validate-before-trusting-it-unattended)
- [Run it for real](#run-it-for-real)
- [Known limitations](#known-limitations)
- [Building the binary yourself](#building-the-binary-yourself)
- [Project layout](#project-layout)

## Features

- **Reads the screen, decides, acts.** OCR (a fast pixel/template match
  first, Tesseract as fallback -- in-process `tesserocr` on Windows when
  available, ~3.76x faster) parses each skill row, checks it against your
  goal, and sends the accept/reject/reroll keys -- no memory reading or
  game files touched.
- **Goals, not guesswork.** Define required skills, protected skills
  (never lose these, no matter what else improves), and multiple
  acceptance profiles (OR logic) -- either through an interactive wizard
  or a plain YAML file.
- **A colored, arrow-key interactive menu.** Launch it with no arguments
  (or just double-click the exe) and get a full-screen menu with live
  status (OCR engine, window, active goal) always visible -- Up/Down or a
  number key to move, Enter to run. Every CLI flag is also reachable this
  way; nothing requires command-line knowledge.
- **Validate before you trust it.** `--dry-run` evaluates a roll and
  prints its verdict without ever touching accept/reject/reroll, so you
  can check the decision logic against real rolls first.
- **Hotkey-driven, not focus-driven.** Position the game, then press a
  global start hotkey when ready -- a separate stop hotkey force-stops a
  run (or cancels before it even starts) without needing focus back on
  the terminal.
- **Cross-platform.** Native Windows, and macOS via CrossOver.
- **Debug-log everything, bundle failures in one command.** Every attempt
  is logged (human-readable + structured JSONL); `--package-failure`
  zips up the relevant log and failure screenshots for a bug report.

## Quick Start (download and run)

No Python or command-line experience needed for this path.

1. Download the latest release for your OS from the
   [Releases page](../../releases) and unzip it.
2. **First launch will be blocked by your OS** -- this is expected for an
   unsigned indie tool, not a sign something's wrong:
   - **macOS**: right-click (or Control-click) `qurio-aug` and choose
     **Open**, then confirm in the dialog that appears (a plain
     double-click will just say it "cannot be opened" and stop there).
     You'll also need to grant two permissions in System Settings >
     Privacy & Security: **Screen Recording** and **Accessibility**, to
     the exact `qurio-aug` file you unzipped (re-grant if you move or
     re-download it).
   - **Windows**: you'll likely see "Windows protected your PC"
     (SmartScreen). Click **More info**, then **Run anyway**. Some
     antivirus software may also flag or quarantine the exe -- this is a
     known false-positive pattern for PyInstaller-built executables, not
     a sign of anything malicious; check your antivirus's quarantine list
     if the exe seems to disappear after download.
3. **Double-click `qurio-aug` to start it.** A colored menu opens showing
   live status (OCR engine, window, active goal) above the command list
   -- Up/Down or a number key to move, Enter to run; nothing needs
   command-line knowledge. Step through it roughly in this order the
   first time:
   1. **Run diagnostics** (`selfcheck`) -- confirms the OCR engine and
      window detection are working before you touch the game.
   2. **Build a new goal config** (`wizard`) -- answers a few questions
      about what skills to farm for and what to protect, and writes the
      config for you. See [Define a goal](#define-a-goal) below for the
      format if you'd rather hand-edit one, and `configs/goals/*.yaml`
      for examples.
   3. **Calibrate against your window** (`calibrate`) -- do this once,
      and again if you resize the game window. See
      [Calibration](#calibration-do-this-first-against-the-live-game)
      below for what to check in its output.
   4. **Test a goal against the current screen** (`dry-run`) -- validates
      your goal against a few real rolls before trusting it unattended
      (see
      [Validate before trusting it unattended](#validate-before-trusting-it-unattended)
      below).
   5. **Start farming with a goal** (`farm`) -- runs the full autonomous
      loop.

   (If you'd rather use command-line flags -- for scripting, or to tune
   timing/hotkeys the menu doesn't expose -- every menu command is also a
   flag; run `qurio-aug --help` from a terminal to see them all. The
   examples through the rest of this README use that flag form.)

If something doesn't work, see [Known limitations](#known-limitations) and
`docs/windows-beta-checklist.md` before reporting an issue -- most early
Windows problems are one of the things listed there. When you do report
one, the interactive menu's `package-failure` command (or `qurio-aug
--package-failure`) bundles the relevant debug log and saved failure
screenshots from `logs/` into one zip to attach, instead of hunting
through the folder by hand.

## Setup (from source / development)

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
brew install tesseract   # macOS. Windows: choco install tesseract, or the
                          # official UB-Mannheim installer
```

macOS additionally needs two permissions granted to whatever process
you'll run this from (Terminal/iTerm/etc.) in System Settings > Privacy &
Security: **Screen Recording** (to capture the game window's pixels) and
**Accessibility** (to send it keystrokes). Windows doesn't gate screen
capture behind a permission prompt, but sending keystrokes may need this
process run as Administrator if the game itself is running elevated.

Regenerate `data/*.json` if the datamined spreadsheet is ever updated to a
newer game version:
```sh
.venv/bin/python scripts/build_data_from_xlsx.py
```

## Calibration (do this first, against the live game)

`configs/regions.yaml` ships with default ROI boxes that should already
be close on any 16:9 setup (the boxes are expressed relative to the
game's actual 16:9 content, with any window letterboxing/pillarboxing
auto-detected and trimmed before they're applied -- see
`qurio_aug/capture/`) -- but window chrome, DPI scaling, or a different
in-game resolution can still shift things slightly. With the game sitting
on an Augmentation Results screen (STATE 4):

```sh
.venv/bin/python -m qurio_aug.calibrate
```
(or `qurio-aug --calibrate`, or the interactive menu's `calibrate`
command, if you're running the compiled build)

This saves the full screenshot plus per-row name/value crops to `logs/`.
Check that each crop cleanly frames just the text it's meant to, and that
the printed OCR reads look sane. Adjust the `[x0, y0, x1, y1]` fractional
boxes in `regions.yaml` and re-run until they do.

## Define a goal

Either run `qurio-aug --wizard` (or `.venv/bin/python -m qurio_aug.main
--wizard` from source) and answer its questions, or copy
`configs/goals/example.yaml` and edit `required_skills`,
`allowed_additional_skills`, and `protected_skills` directly for what
you're farming -- see that file's comments for the field semantics, and
`configs/goals/hellfire_strife.yaml` for a more advanced multi-profile
example. Every skill name in a goal config is checked against the
canonical skill list when it loads (and, in the wizard, as you type) --
a typo gets caught immediately with a suggested correction instead of
silently loading a config that will never match anything.

## Validate before trusting it unattended

With the game on a real Augmentation Results screen, run one evaluation
at a time and compare its verdict to what you'd decide by eye. This never
sends the accept/reject/reroll macros (won't touch materials or change
what's applied), but may send Q/E to page through a multi-page roll while
reading it:

```sh
qurio-aug --goal configs/goals/your-goal.yaml --dry-run
```

Trigger a manual reroll in-game and re-run the command for the next roll.
Do this for a handful of rolls, including any with a full 6-7 augments
(to exercise pagination) and any removed-skill ("None") cases, before
moving on.

## Run it for real

Get the game to STATE 1 (Material Select, correct armor piece + augment
type already chosen), then:

```sh
qurio-aug --goal configs/goals/your-goal.yaml
```

This prints a ready message and waits for the **start hotkey** (**Control+M**
on macOS, **Alt+M** on Windows -- different defaults per platform because
each avoids a different real collision with the game's own bindings, see
`qurio_aug/hotkeys.py`) rather than firing immediately -- position the game
window with no time pressure, then press it when ready. The **stop hotkey**
(**Control+N** / **Alt+N**) force-stops a running loop at any point (checked
before every keypress and during every wait), without needing to switch
focus back to the terminal -- switching focus mid-run to force a stop the
old way could leave the game stuck mid-dialog. Pressed *before* the start
hotkey, it cancels back out instead of running. Pass `--start-hotkey`/
`--stop-hotkey` to remap either one, or `--no-hotkeys` to fall back to a
fixed `--start-delay` countdown with no stop hotkey at all.

It loops read -> decide -> accept/reroll autonomously until the goal is
met, `--max-attempts` (default 300) is hit, or the stop hotkey is pressed,
logging every attempt to `logs/<goal>-<timestamp>.log` (human-readable)
and `.jsonl` (structured). A long run shows a live, in-place progress
readout (attempt count, rough rate, elapsed time) rather than one line
scrolling past per attempt; an accepted roll or a suspiciously large
delta (a possible OCR misread, worth double-checking against the game)
always prints its own line immediately.

### Tuning speed for long runs

`--press-hold`, `--post-press-delay`, and `--settle-delay` control the
timing between keypresses and before each screen capture -- the defaults
were halved from an early, deliberately conservative baseline, not
measured minimums. `--post-press-delay` is the one to be careful with: it
stands in for "has the game's menu transition settled", and cutting it
too far risks a macro's later keypress landing mid-transition on the
wrong screen -- a failure that desyncs silently rather than raising an
error. `--settle-delay` (waiting for the sparkle decoration to fade
before the first OCR read) is lower-risk to cut, since the worst case is
just one extra retry rather than a state desync. Validate any faster
setting with `--max-attempts 10-20` first, watching that the game
actually lands on the expected screen each time, before trusting it for
a long (500+) run.

## Known limitations

| Platform | Limitation |
|---|---|
| Windows | **No window-occlusion protection.** The capture backend grabs a fixed screen region, so anything drawn on top during a run (another window, an overlay, a notification toast) gets captured instead of the game, with no error -- just wrong OCR input. Keep the game window unobstructed and on top for the duration of a run. macOS captures by window ID instead, which isn't affected. |
| Windows | **Borderless windowed only.** Exclusive fullscreen isn't supported -- the game needs to be in borderless windowed mode (the common setup for exactly this reason: it's much easier for other tools to interact with). |
| Both | **16:9 only.** Ultrawide/21:9 in-game rendering isn't supported. |
| Both | Keep the game window at a stable size/position for the duration of a run -- resizing or moving it mid-run isn't handled. |

## Building the binary yourself

```sh
pip install pyinstaller
brew install dylibbundler   # macOS only
./packaging/vendor_tesseract_macos.sh     # macOS
# or: choco install tesseract -y; powershell -File packaging/vendor_tesseract_windows.ps1   # Windows
pyinstaller packaging/qurio-aug.spec --noconfirm
./dist/qurio-aug --selfcheck
```

Produces `dist/qurio-aug` as a standalone executable, with Tesseract and
all runtime data (skill list, digit templates, region config, example
goals) bundled in -- no separate Tesseract install needed to run it. See
`qurio_aug/tesseract_setup.py` and the vendoring scripts' comments for
how that's wired up.

## Project layout

- `qurio_aug/capture/` -- window discovery + pixel capture, with a
  platform backend each for macOS (Quartz) and Windows (pywin32 + mss),
  plus the shared 16:9 content-rect detection that makes ROI boxes work
  across different window sizes/letterboxing
- `qurio_aug/ocr.py` -- crop -> digit template match / Tesseract -> parsed
  skill rows (Windows: OCR calls prefer the in-process `tesserocr`
  accelerator when available, ~3.76x faster, falling back to the
  subprocess path otherwise -- see `docs/ocr-performance-research.md` #1b)
- `qurio_aug/skills_db.py` -- canonical skill list + fuzzy-match OCR noise
- `qurio_aug/decision.py` -- accept/reject rules (no I/O, unit-testable)
- `qurio_aug/goal_config.py` -- goal YAML loading + eager skill-name validation
- `qurio_aug/goal_wizard.py` -- interactive goal-config builder (`--wizard`)
  and editor for an existing one (interactive menu only, `edit` command)
- `qurio_aug/tui.py` -- the shared arrow-key menu widget and colored
  prompt/output helpers used everywhere in the interactive menu
- `qurio_aug/input.py` -- keyboard macros for the confirmed UI flow
- `qurio_aug/hotkeys.py` -- global start/force-stop hotkeys (Control+M/N on
  macOS, Alt+M/N on Windows)
- `qurio_aug/state_machine.py` -- wires the above into the full loop
- `qurio_aug/paths.py` / `qurio_aug/tesseract_setup.py` -- resource-path
  and bundled-Tesseract resolution for both source and compiled runs
- `qurio_aug/calibrate.py` / `main.py` -- CLI entry points
- `qurio_aug/support_bundle.py` -- bundles the most recent debug log +
  failure screenshots from `logs/` into one zip (`--package-failure`)
- `packaging/` -- PyInstaller spec + per-OS Tesseract vendoring scripts
- `.github/workflows/build.yml` -- CI: tests + compiled builds on every
  push, published to GitHub Releases on a version tag
- `data/` -- generated from the datamined xlsx (armor pools, skill list)
- `data/digit_templates/` -- reference glyph crops for the fast digit-match
  path; add more `<digit>.png` files here if a new digit ever proves
  troublesome for Tesseract
- `configs/goals/*.yaml` -- example/target-skill configs
- `configs/regions.yaml` -- calibrated OCR regions
- `docs/windows-beta-checklist.md` -- what to check/paste when reporting a
  Windows-side problem
- `tests/` -- offline unit tests, no live game needed. Run them all:
  `for f in tests/test_*.py; do .venv/bin/python "$f" || break; done`
