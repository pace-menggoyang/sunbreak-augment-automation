"""Per-attempt logging: one human-readable line + one JSONL record per
augment attempt. Skills only -- defense/resistance/slots are read off
screen incidentally by OCR but never logged, matching the decision
engine's scope (see decision.py).
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from qurio_aug.decision import Decision, Goal


def _format_skill(r) -> str:
    if r.removed:
        return f"{r.name} (removed)"
    sign = "+" if r.delta >= 0 else ""
    marker = " [?!]" if r.is_suspicious else ""
    return f"{r.name} {sign}{r.delta}{marker}"


def format_line(attempt: int, decision: Decision) -> str:
    added = ", ".join(_format_skill(r) for r in decision.added) or "-"
    removed = ", ".join(_format_skill(r) for r in decision.removed) or "-"
    outcome = "ACCEPTED" if decision.accepted else "rejected"
    line = f"[attempt #{attempt}] added: {added} | removed: {removed} | {outcome} ({decision.reason})"

    suspicious = [r for r in decision.added + decision.removed if r.is_suspicious]
    if suspicious:
        names = ", ".join(f"{r.name} ({r.delta:+d})" for r in suspicious)
        line += (
            f"\n  [?!] UNUSUALLY LARGE DELTA -- possible OCR misread, verify "
            f"against the game before trusting: {names}"
        )
    return line


@dataclass
class AttemptLogger:
    goal: Goal
    log_dir: Path = Path("logs")

    def __post_init__(self) -> None:
        self.log_dir.mkdir(exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self._txt_path = self.log_dir / f"{self.goal.name}-{stamp}.log"
        self._jsonl_path = self.log_dir / f"{self.goal.name}-{stamp}.jsonl"
        self._debug_path = self.log_dir / f"{self.goal.name}-{stamp}.debug.log"
        self.attempt = 0

    def debug(self, message: str) -> None:
        """Step-by-step trace of what state_machine.py is actually doing
        while reading a roll -- page indicator reads, navigation,
        retries, the reasoning behind a doomed/already_rejected decision
        -- written to its own file so that context survives even if the
        terminal running this gets closed or scrolled past. This was the
        actual gap behind several live debugging sessions where a
        failure had to be reverse-engineered from a single saved
        screenshot after the fact, with no record of what the code
        believed was happening in the moments leading up to it. Kept
        separate from the human-readable per-attempt .log (which only
        records completed decisions) since this is much higher-volume
        and mostly only useful once something's already gone wrong, not
        for a normal skim of "what happened this run."
        """
        ts = time.strftime("%H:%M:%S") + f".{int(time.time() * 1000) % 1000:03d}"
        with self._debug_path.open("a") as f:
            f.write(f"[{ts}] [attempt #{self.attempt + 1}] {message}\n")

    def log(self, decision: Decision) -> str:
        self.attempt += 1
        line = format_line(self.attempt, decision)

        with self._txt_path.open("a") as f:
            f.write(line + "\n")

        record = {
            "attempt": self.attempt,
            "timestamp": time.time(),
            "goal": self.goal.name,
            "augment_type": self.goal.augment_type,
            "accepted": decision.accepted,
            "reason": decision.reason,
            "added": [asdict(r) for r in decision.added],
            "removed": [asdict(r) for r in decision.removed],
        }
        with self._jsonl_path.open("a") as f:
            f.write(json.dumps(record) + "\n")

        return line
