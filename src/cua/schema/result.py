from __future__ import annotations

from .common import StrictModel


class StepTrace(StrictModel):
    step_id: str
    resolved_tier: str | None
    elapsed_ms: int


class ReplayResult(StrictModel):
    """What a replay run answered. `outputs` holds only the values the fired outcome
    binds, so a business outcome with `binds: []` returns an empty mapping even if
    earlier steps had read something."""

    capability_id: str
    version: str
    outcome: str
    outputs: dict[str, str]
    steps: list[StepTrace]
