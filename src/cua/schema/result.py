from __future__ import annotations

from enum import Enum

from pydantic import model_validator

from .common import StrictModel


class ReplayStatus(str, Enum):
    success = "success"
    business_outcome = "business_outcome"
    failed = "failed"


class FailureKind(str, Enum):
    session_expired = "session_expired"
    permission_denied = "permission_denied"
    interstitial_persisted = "interstitial_persisted"
    timeout = "timeout"
    target_drift = "target_drift"
    checkpoint_failed = "checkpoint_failed"
    surface_error = "surface_error"
    policy_denied = "policy_denied"
    approval_required = "approval_required"


class Failure(StrictModel):
    kind: FailureKind
    step_id: str
    expected: str
    observed: str


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
    earlier steps had read something."""

    capability_id: str
    version: str
    status: ReplayStatus
    outcome: str | None
    outputs: dict[str, str]
    steps: list[StepTrace]
    failure: Failure | None

    @model_validator(mode="after")
    def _check_shape(self) -> "ReplayResult":
        failed = self.status is ReplayStatus.failed
        if failed != (self.failure is not None):
            raise ValueError("status 'failed' and a failure block imply each other")
        if failed == (self.outcome is not None):
            raise ValueError("an outcome is present exactly when the run did not fail")
        return self
