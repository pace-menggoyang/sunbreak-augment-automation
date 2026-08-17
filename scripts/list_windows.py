"""One-off diagnostic: list visible window owners/titles, to figure out
what window_title_hint to use in configs/regions.yaml for the game window
running under CrossOver.

Run with the game open:
  .venv/bin/python scripts/list_windows.py
"""
import Quartz

window_list = Quartz.CGWindowListCopyWindowInfo(
    Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID
)
for w in window_list:
    owner = w.get("kCGWindowOwnerName", "") or ""
    title = w.get("kCGWindowName", "") or ""
    if owner and owner not in ("Window Server", "Dock"):
        print(f"owner={owner!r} title={title!r}")
