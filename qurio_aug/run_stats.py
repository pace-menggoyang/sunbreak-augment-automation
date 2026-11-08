"""Run-wide counters for a farming run, summarized once when it ends.

Exists because diagnosing the Windows beta logs meant hand-building shell
pipelines to answer simple questions ("how often does the template match
fail?", "how many attempts burned the full indicator retries?"). Printing
and logging these at the end of every run answers them directly, and makes
a bug report self-explanatory.

Pure data + formatting -- no I/O, so it's trivially unit-testable.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field


def _digit_family(source: str) -> str:
    """"tesseract:psm8" / "tesseract:psm7!" -> "tesseract"; others unchanged."""
    return source.rstrip("!").split(":", 1)[0]


def _counts(counter: Counter) -> str:
    return ", ".join(f"{name} {n}" for name, n in counter.most_common())


@dataclass
class RunStats:
    page_retries: int = 0  # content re-reads after an unparseable row
    indicator_retries: int = 0  # re-captures while the page indicator was ambiguous
    indicator_unresolved: int = 0  # indicator still ambiguous after every retry
    dialogs: Counter = field(default_factory=Counter)  # dialog kind -> times dismissed
    delay_from: float | None = None  # key delay before the first auto-slowdown
    delay_to: float | None = None  # key delay after the last one
    digit_sources: Counter = field(default_factory=Counter)
    name_sources: Counter = field(default_factory=Counter)
    implausible_digits: int = 0  # digits above the plausible delta that nothing could correct ("!")
    speed: str = ""  # label of the speed profile in use, "" if normal/unspecified
    reverted_after_dialogs: int | None = None  # set if a fast run switched itself back to normal

    def record_page(self, page) -> None:
        """Tallies how each parsed row's digit and name were read."""
        for row in page:
            source = getattr(row, "digit_source", "")
            if source:
                self.digit_sources[_digit_family(source)] += 1
                if source.endswith("!"):
                    self.implausible_digits += 1
            name_source = getattr(row, "name_source", "")
            if name_source:
                self.name_sources[name_source] += 1

    def record_slowdown(self, old: float, new: float) -> None:
        if self.delay_from is None:
            self.delay_from = old
        self.delay_to = new

    def summary_lines(self, attempts: int, elapsed: float) -> list[str]:
        mins, secs = divmod(int(elapsed), 60)
        rate = attempts / elapsed * 60 if elapsed > 0 else 0.0
        lines = [f"Run summary: {attempts} attempt(s) in {mins}m{secs:02d}s ({rate:.1f}/min)"]
        if self.speed:
            text = f"speed: {self.speed}"
            if self.reverted_after_dialogs is not None:
                text += f" -- switched back to Normal after {self.reverted_after_dialogs} stray dialogs"
            lines.append("  " + text)

        if self.digit_sources:
            text = _counts(self.digit_sources)
            if self.implausible_digits:
                text += f" -- {self.implausible_digits} implausible (+3 or more, kept as-is; see '!' in the debug log)"
            lines.append(f"  digit reads: {text}")
        if self.name_sources:
            lines.append(f"  name reads needing recovery: {_counts(self.name_sources)}")

        retries = []
        if self.page_retries:
            retries.append(f"page reads {self.page_retries}")
        if self.indicator_retries:
            unresolved = f" ({self.indicator_unresolved} unresolved)" if self.indicator_unresolved else ""
            retries.append(f"page indicator {self.indicator_retries}{unresolved}")
        if retries:
            lines.append("  retries: " + ", ".join(retries))

        if self.dialogs:
            text = ", ".join(f"{kind} x{n}" for kind, n in self.dialogs.most_common())
            if self.delay_to is not None:
                text += f" | key delay raised {self.delay_from:.3f}s -> {self.delay_to:.3f}s"
            lines.append(f"  dialogs dismissed: {text}")
        return lines
