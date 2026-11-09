"""Runtime settings. Kept deliberately small and explicit."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Budgets(BaseModel):
    """Per-incident hard limits. On exhaustion the agent escalates."""

    max_steps: int = 25
    max_wall_clock_seconds: int = 600
    max_model_tokens: int = 100_000


class Settings(BaseModel):
    data_dir: str = "./data"
    # How much detail the logs carry: DEBUG, INFO, WARNING or ERROR.
    log_level: str = "INFO"
    budgets: Budgets = Field(default_factory=Budgets)
    # "mock" runs the deterministic development model; no API key needed.
    model_backend: str = "mock"
