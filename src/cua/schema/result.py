from __future__ import annotations

from enum import Enum

from pydantic import model_validator

from .common import StrictModel
from .failure import Failure, FailureKind
from .intervention import InterventionRecord

__all__ = ["Failure", "FailureKind", "Recovery", "ReplayResult", "ReplayStatus", "StepTrace"]


class ReplayStatus(str, Enum):
    success = "success"
    business_outcome = "business_outcome"
    failed = "failed"


class Recovery(StrictModel):
    """An interstitial that was dismissed during a step. A recovered step carries
    these on its trace; anything in `ReplayResult.failure` is by definition unrecovered."""

    interstitial: str
    attempt: int


class StepTrace(StrictModel):
    step_id: str
    resolved_tier: str | None
    elapsed_ms: int
    recoveries: list[Recovery]


class ReplayResult(StrictModel):
    """What a replay run answered. `outputs` holds only the values the fired outcome
    binds, so a business outcome with `binds: []` returns an empty mapping even if
    earlier steps had read something. `interventions` lists every human handoff in
    order; a run that was resumed after one still reports its outcome normally."""

    run_id: str
    capability_id: str
    version: str
    status: ReplayStatus
    outcome: str | None
    outputs: dict[str, str]
    steps: list[StepTrace]
    interventions: list[InterventionRecord]
    failure: Failure | None

    @model_validator(mode="after")
    def _check_shape(self) -> "ReplayResult":
        failed = self.status is ReplayStatus.failed
        if failed != (self.failure is not None):
            raise ValueError("status 'failed' and a failure block imply each other")
        if failed == (self.outcome is not None):
            raise ValueError("an outcome is present exactly when the run did not fail")
        return self
