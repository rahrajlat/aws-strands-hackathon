"""Settings from environment variables (and .env if present)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

EARTH_PROVIDERS = ("ollama", "bedrock", "mock")
DECIDER_MODES = ("auto", "server", "mock")


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    earth_provider: str = "ollama"
    earth_model: str = "gpt-oss:120b-cloud"
    ollama_host: str = "http://localhost:11434"
    ollama_api_key: str = ""
    aws_region: str = "us-east-1"
    bedrock_price_in_per_m: float = 0.0
    bedrock_price_out_per_m: float = 0.0
    decider_url: str = "http://127.0.0.1:8099"
    decider_mode: str = "auto"
    confidence_threshold: float = 0.70
    light_delay_min: float = 12.0  # displayed one-way delay (simulated minutes)
    delay_real_seconds: float = 15.0  # how long that takes in real time
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def has_bedrock_prices(self) -> bool:
        return self.bedrock_price_in_per_m > 0 or self.bedrock_price_out_per_m > 0


def load_settings(earth: str | None = None, decider: str | None = None) -> Settings:
    """Read settings; CLI overrides (`earth`, `decider`) win over the environment."""
    load_dotenv()
    env = os.environ.get
    provider = (earth or env("ROVER_EARTH_PROVIDER", "ollama")).lower()
    if provider not in EARTH_PROVIDERS:
        raise ValueError(f"unknown Earth provider {provider!r}; use one of {EARTH_PROVIDERS}")
    mode = (decider or env("ROVER_DECIDER", "auto")).lower()
    if mode not in DECIDER_MODES:
        raise ValueError(f"unknown decider mode {mode!r}; use one of {DECIDER_MODES}")
    default_model = (
        "gpt-oss:120b-cloud" if provider != "bedrock" else "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    )
    return Settings(
        earth_provider=provider,
        earth_model=env("ROVER_EARTH_MODEL") or default_model,
        ollama_host=env("OLLAMA_HOST", "http://localhost:11434"),
        ollama_api_key=env("OLLAMA_API_KEY", ""),
        aws_region=env("AWS_REGION", "us-east-1"),
        bedrock_price_in_per_m=_float("BEDROCK_PRICE_IN_PER_M", 0.0),
        bedrock_price_out_per_m=_float("BEDROCK_PRICE_OUT_PER_M", 0.0),
        decider_url=env("STRANDS_DECIDER_URL", "http://127.0.0.1:8099"),
        decider_mode=mode,
        confidence_threshold=_float("ROVER_CONFIDENCE_THRESHOLD", 0.70),
        light_delay_min=_float("ROVER_LIGHT_DELAY_MIN", 12.0),
        delay_real_seconds=_float("ROVER_DELAY_REAL_SECONDS", 15.0),
    )
