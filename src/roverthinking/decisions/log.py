"""JSONL decision log: one line per decision, plus a later line for its outcome."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class DecisionLog:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._n = 0
        self._lock = threading.Lock()
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def new(cls, directory: Path = Path("logs"), prefix: str = "decisions") -> DecisionLog:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return cls(directory / f"{prefix}-{stamp}.jsonl")

    def _write(self, record: dict[str, Any]) -> None:
        if self.path is None:
            return
        with self._lock, self.path.open("a") as f:
            f.write(json.dumps(record, default=str) + "\n")

    def decision(self, data: dict[str, Any], snapshot: dict[str, Any] | None = None) -> int:
        """Log a decision event's data; returns its id for the outcome line."""
        self._n += 1
        rec = {
            "type": "decision",
            "id": self._n,
            "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "tick": data.get("tick"),
            "question": data.get("question"),
            "options": data.get("options"),
            "choice": data.get("choice"),
            "confidence": data.get("confidence"),
            "latency_ms": data.get("latency_ms"),
            "escalated": bool(data.get("escalated")),
            "acted_under_uncertainty": bool(data.get("under_uncertainty")),
            "source": data.get("planner"),
            "hazard": data.get("hazard"),
            "outcome": None,
            "state": snapshot,
        }
        self._write(rec)
        return self._n

    def outcome(self, decision_id: int, outcome: str, **extra: Any) -> None:
        self._write({"type": "outcome", "decision": decision_id, "outcome": outcome, **extra})
