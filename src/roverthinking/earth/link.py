"""The light-delay link: a message queue in both directions. Real-time, no UI imports.

A message is "in flight" for `delay` sim ticks. If the rover's comms are down when it
would arrive (solar flare), it waits and lands as soon as the link comes back.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

TO_EARTH = "to_earth"
TO_MARS = "to_mars"


@dataclass
class Message:
    id: int
    direction: str
    kind: str  # escalation | reply | order | ack | scenario
    text: str
    sent_at: float
    arrive_at: float
    payload: dict[str, Any] = field(default_factory=dict)

    def progress(self, now: float) -> float:
        span = max(self.arrive_at - self.sent_at, 1e-6)
        return max(0.0, min(1.0, (now - self.sent_at) / span))


class EarthLink:
    def __init__(self, delay: float = 15.0) -> None:
        self.delay = delay
        self.up = True
        self._next_id = 1
        self._flight: list[Message] = []

    def send(
        self,
        direction: str,
        kind: str,
        text: str,
        now: float,
        payload: dict[str, Any] | None = None,
    ) -> Message:
        msg = Message(self._next_id, direction, kind, text, now, now + self.delay, dict(payload or {}))
        self._next_id += 1
        self._flight.append(msg)
        return msg

    def in_flight(self) -> list[Message]:
        return list(self._flight)

    def due(self, now: float) -> list[Message]:
        """Pop messages that have arrived. While the link is down nothing is delivered."""
        if not self.up:
            return []
        arrived = [m for m in self._flight if m.arrive_at <= now]
        for m in arrived:
            self._flight.remove(m)
        return arrived

    def eta(self, now: float) -> float | None:
        """Seconds until the next arrival, or None when nothing is in flight."""
        return min((max(0.0, m.arrive_at - now) for m in self._flight), default=None)
