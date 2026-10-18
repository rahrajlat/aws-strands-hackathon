"""Earth Mission Control's desk: every message that crosses the light delay. No UI imports.

Orders go rover-ward, escalations go Earth-ward, replies come back. When the rover's planner can
vet an order (the Steward's Stale Order Check) it does so on arrival; otherwise orders just run.
"""

from __future__ import annotations

from concurrent.futures import Executor
from typing import Any

from roverthinking.earth import mission_control as mc
from roverthinking.earth.link import TO_EARTH, TO_MARS, EarthLink, Message
from roverthinking.earth.providers import EarthModel
from roverthinking.sim import actions
from roverthinking.sim.rover import Rover


class EarthDesk:
    def __init__(
        self, rover: Rover, link: EarthLink, earth: EarthModel, pool: Executor | None = None
    ) -> None:
        self.rover = rover
        self.link = link
        self.earth = earth
        self.pool = pool
        self.planner: Any = None
        self._futures: list[tuple[Any, dict[str, Any]]] = []
        self.earth_calls = 0
        self.earth_latency_ms = 0.0
        self.earth_tokens_in = 0
        self.earth_tokens_out = 0

    # ---- outgoing ---------------------------------------------------------------------------

    def send_order(self, text: str) -> str:
        """Strict grammar only. Returns an error message, or '' once the order is on its way."""
        try:
            order = mc.parse_order(text)
        except mc.OrderError as exc:
            self.rover.emit("order_invalid", text=text, reason=str(exc))
            return str(exc)
        self.send_parsed(order)
        return ""

    def send_parsed(self, order: mc.Order) -> None:
        self.link.send(
            TO_MARS, "order", order.text, self.rover.tick_n, {"action": order.action, **order.args}
        )
        self.rover.emit("msg_sent", direction=TO_MARS, kind="order", text=order.text)

    def escalate(self, text: str, payload: dict[str, Any]) -> None:
        self.link.send(TO_EARTH, "escalation", text, self.rover.tick_n, payload)
        self.rover.emit("msg_sent", direction=TO_EARTH, kind="escalation", text=text)

    # ---- once per sim tick ------------------------------------------------------------------

    def tick(self) -> None:
        r = self.rover
        self.link.up = r.comms_ok
        for msg in self.link.due(r.tick_n):
            self._deliver(msg)
        for item in list(self._futures):
            fut, payload = item
            if fut.done():
                self._futures.remove(item)
                self._reply(fut.result(), payload)

    # ---- delivery ---------------------------------------------------------------------------

    def _deliver(self, msg: Message) -> None:
        r = self.rover
        r.emit("msg_received", direction=msg.direction, kind=msg.kind, text=msg.text)
        if msg.direction == TO_MARS and msg.kind == "order":
            self._on_order(msg)
        elif msg.direction == TO_MARS and msg.kind == "reply":
            if hasattr(self.planner, "on_reply"):
                self.planner.on_reply(r, msg.payload)
        elif msg.direction == TO_EARTH and msg.kind == "escalation":
            self._on_escalation(msg)

    def _on_order(self, msg: Message) -> None:
        r = self.rover
        name = msg.payload["action"]
        args = {k: v for k, v in msg.payload.items() if k != "action"}

        def execute() -> None:
            if name == "sample":
                rocks = [h for h in r.detected_hazards() if h.kind == "strange_rock"]
                if rocks:
                    res = actions.sample(r, rocks[0].id, args["instrument"])
                else:
                    res = actions._reject(r, "sample", "no rock in view")
            else:
                res = actions.dispatch(r, name, **args)
            r.emit("order_result", text=msg.text, ok=res.ok, reason=res.reason)
            self._ack(msg.text, "executed" if res.ok else f"rejected: {res.reason}")

        def hold(reason: str) -> None:
            r.emit("order_held", text=msg.text, reason=reason)
            self._ack(msg.text, f"held: {reason}")

        vet = getattr(self.planner, "vet_order", None)
        if vet is not None:
            vet(r, msg.text, name, execute, hold)
        else:
            execute()

    def _ack(self, text: str, outcome: str) -> None:
        line = f"{text}: {outcome}"
        self.link.send(TO_EARTH, "ack", line, self.rover.tick_n)
        self.rover.emit("msg_sent", direction=TO_EARTH, kind="ack", text=line)

    def _on_escalation(self, msg: Message) -> None:
        p = msg.payload
        work = lambda: self.earth.answer_escalation(  # noqa: E731
            p["snapshot"], p["question"], p["options"], p["local_choice"], p["local_confidence"]
        )
        self.earth_calls += 1
        if self.pool is None:
            self._reply(work(), p)
        else:
            self._futures.append((self.pool.submit(work), p))

    def _reply(self, reply: Any, payload: dict[str, Any]) -> None:
        self.earth_latency_ms += reply.latency_ms
        self.earth_tokens_in += reply.tokens_in
        self.earth_tokens_out += reply.tokens_out
        text = reply.message or f"{reply.recommendation}: {reply.reasoning}"
        self.link.send(
            TO_MARS,
            "reply",
            text,
            self.rover.tick_n,
            {**payload, "recommendation": reply.recommendation, "reasoning": reply.reasoning},
        )
        self.rover.emit("msg_sent", direction=TO_MARS, kind="reply", text=text)
