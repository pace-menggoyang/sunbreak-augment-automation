# Windows beta tester checklist

This tool was built and tested entirely on macOS. The Windows build has
never run on real Windows hardware before it reaches you -- you're the
first real test of that code path. This checklist is what to run and
paste back so a problem can actually be diagnosed, rather than just
reported as "it didn't work."

## 1. System info

Paste:
- Windows version/build (Settings > System > About)
- Display resolution and scale % (Settings > System > Display)
- Single monitor or multiple?
- Steam or Game Pass version of the game?
- Antivirus product, if any

## 2. Selfcheck

```
qurio-aug.exe --selfcheck
```

Paste the full output. This checks the OCR engine and window-capture
backend independently of the game -- if this fails, nothing else will
work either, and the output usually says why.

## 3. List windows

With the game running:
```
qurio-aug.exe --list-windows
```

Paste the full output, especially the line for the game itself. If the
game isn't found automatically later, this is what tells us what
`--window` value to use instead.

## 4. Calibration

With the game sitting on an Augmentation Results screen:
```
qurio-aug.exe --calibrate
```

Paste the console output, and attach the saved crop images from the
`logs/` folder next to the exe. These show exactly what the tool is
seeing -- if a crop doesn't cleanly frame the name/value text, that's the
ROI boxes needing recalibration for your setup, not a code bug.

## 5. Dry run

```
qurio-aug.exe --goal configs\goals\example.yaml --dry-run
```

Run this once against a normal (single-page) roll, and once against a
roll with 4+ skill entries (multi-page, to exercise pagination). Paste
the full console output for both.

## 6. Hotkeys

Confirm Alt+M starts and Alt+N force-stops as expected, and note whether
either one visibly affected the game itself. (Windows defaults to Alt,
not Control, specifically because Control collided with Sunbreak's own
"Skill Info" bind -- confirmed live, see `qurio_aug/hotkeys.py`. If Alt
turns out to affect something too on your setup, that's exactly the kind
of thing this check is for.)

## 7. Anything that crashed

Paste the **full** traceback, not a summary or a screenshot of just the
last line -- the actual error is almost always higher up. Include the
exact command you ran. Also run `qurio-aug.exe --package-failure` (or the
interactive menu's `package-failure` command) and attach the zip it
produces -- bundles the relevant debug log and any saved failure
screenshots from `logs/` for you.
