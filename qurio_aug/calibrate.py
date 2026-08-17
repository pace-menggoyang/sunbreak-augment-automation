"""Interactive calibration tool -- run this against the live game before
trusting the automation, per Phase 0 of the plan. It:

  1. Finds the CrossOver game window and reports its size (so you can
     confirm the right window was picked).
  2. Grabs a screenshot and saves it to logs/calibration_full.png.
  3. Reads the page indicator (if any) to figure out which row template
     applies to the current screen, then crops each of that template's
     skill rows (name_box + value_box) using configs/regions.yaml and
     saves them individually to logs/calibration_row{N}_{name,value}.png,
     auto-cropped to detected text content the same way ocr.py does, so
     you can eyeball whether the boxes actually land on the right text.

The default regions.yaml was measured off 2870x1800 reference
screenshots -- your window is almost certainly a different size/position,
so expect to need a few rounds of: run this, look at the crops, nudge the
fractional boxes in regions.yaml, repeat. Run it once against a
single-page roll, once against page 1 of a multi-page roll, and once
after paging to page 2+, since each uses a different row template.
"""
from __future__ import annotations

import sys
from pathlib import Path

from qurio_aug import capture, ocr
from qurio_aug.tesseract_setup import configure_tesseract

LOG_DIR = Path("logs")


def main() -> None:
    configure_tesseract()
    title_hint = sys.argv[1] if len(sys.argv) > 1 else None
    region_config = ocr.load_region_config()
    hint = title_hint or region_config.window_title_hint

    print(f"looking for a window matching {hint!r} ...")
    matches = capture.find_windows(hint)
    if not matches:
        print(f"no window found. Pass a different hint, e.g.:\n"
              f"  python -m qurio_aug.calibrate 'Monster Hunter'")
        sys.exit(1)
    if len(matches) > 1:
        print(f"found {len(matches)} matches, using the first -- pass a more "
              f"specific hint if this is wrong:")
        for m in matches:
            print(f"  - owner={m.owner_name!r} title={m.title!r} bounds={m.bounds}")
    window = matches[0]
    print(f"using: owner={window.owner_name!r} title={window.title!r} "
          f"bounds={window.bounds}")

    screenshot = capture.screenshot_window(window)
    LOG_DIR.mkdir(exist_ok=True)
    full_path = LOG_DIR / "calibration_full.png"
    screenshot.save(full_path)
    print(f"saved full screenshot -> {full_path} ({screenshot.size[0]}x{screenshot.size[1]})")

    indicator_crop = ocr._crop_fraction(screenshot, region_config.page_indicator_box)
    indicator_crop.save(LOG_DIR / "calibration_page_indicator.png")
    indicator = ocr.read_page_indicator(screenshot, region_config)
    if indicator is None:
        template_name = "single_page"
        print("no page indicator detected -> using 'single_page' row template")
    else:
        current, total = indicator
        template_name = "first_of_multi" if current == 1 else "continuation"
        print(f"page indicator reads {current}/{total} -> using {template_name!r} row template")
    print(f"  crop -> {LOG_DIR / 'calibration_page_indicator.png'}")

    template = region_config.row_templates[template_name]
    for i, row in enumerate(template):
        raw = ocr.read_row(screenshot, row)
        parsed = ocr.parse_row(raw)
        print(f"row {i}: name_ocr={raw.name_text!r} value_ocr={raw.value_text!r} "
              f"digit_ocr={raw.digit_text!r} -> {parsed}")

        for label, box in (("name", row.name_box), ("value", row.value_box)):
            crop = ocr._crop_fraction(screenshot, box)
            bbox = ocr._bright_bbox(crop)
            if bbox is not None:
                crop = crop.crop(ocr._pad_bbox(bbox, crop))
            out_path = LOG_DIR / f"calibration_row{i}_{label}.png"
            ocr._upscale(crop).save(out_path)
            print(f"  {label} crop -> {out_path}" + ("" if bbox is not None else " (blank)"))

    print("\nInspect the saved crops. If a box doesn't cleanly frame just "
          "the name or just the value text (or a crop came back blank when "
          "it shouldn't have), adjust the matching fractional box for "
          f"row_templates.{template_name} in configs/regions.yaml (values "
          "are [x0, y0, x1, y1] as fractions of the full window) and "
          "re-run. Boxes can be generous -- OCR auto-crops to the actual "
          "text within them -- just don't let one bleed into a neighboring "
          "row.")


if __name__ == "__main__":
    main()
