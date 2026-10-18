"""The structured answers Earth must give. Strands validates the model's output against these."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from roverthinking.sim.hazards import HAZARD_KINDS
from roverthinking.sim.rover import INSTRUMENTS

HazardKind = Literal[HAZARD_KINDS]  # type: ignore[valid-type]
Instrument = Literal[INSTRUMENTS]  # type: ignore[valid-type]


class EscalationAnswer(BaseModel):
    """Earth's reply to a rover that is unsure what to do."""

    recommendation: str = Field(description="Exactly one of the option names offered.")
    reasoning: str = Field(description="One or two sentences on why, naming the key facts.")
    message: str = Field(description="A short in-character radio line: 'Mission Control to Rover...'")


class OrderPlan(BaseModel):
    """A free-text order from the player, reduced to one supported command."""

    understood: bool = Field(description="False if the text is not an order the rover can run.")
    action: Literal["stop", "drive", "safe_mode", "shield", "unshield", "back_up", "sample", "none"]
    speed: Literal["normal", "slow"] = "normal"
    instrument: Instrument = "spectrometer"
    explanation: str = Field(description="What you understood, or why you could not.")


class ScenarioEventPlan(BaseModel):
    kind: HazardKind
    severity: int = Field(ge=1, le=3, description="1 minor, 2 major, 3 catastrophic")
    ahead_m: float | None = Field(
        default=None, description="Metres ahead of the rover, for terrain hazards and rocks only."
    )
    after_seconds: float = Field(ge=0, le=300, description="Seconds from the start of the scenario.")


class ScenarioPlan(BaseModel):
    """A free-text scenario turned into a schedule of supported hazards."""

    title: str = Field(description="Two to five words.")
    description: str = Field(description="One sentence.")
    events: list[ScenarioEventPlan] = Field(max_length=8)
