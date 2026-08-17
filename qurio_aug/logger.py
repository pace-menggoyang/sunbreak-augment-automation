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
        self.attempt = 0

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
