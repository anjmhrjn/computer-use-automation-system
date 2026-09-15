"""What crosses between a paused replay and a human operator. The request carries
only what the console may show: redacted failure text and parameter *names*. The
resolution is operator input and is validated as such."""

from __future__ import annotations

from enum import Enum

from pydantic import model_validator

from .common import StrictModel
from .failure import Failure, FailureKind


class InterventionRequest(StrictModel):
    request_id: str
    run_id: str
    capability_id: str
    step_id: str
    failure: Failure
    location: str | None
    resumable_steps: list[str]
    inputs: list[str]
    requested_at: str


class ResolutionKind(str, Enum):
    retry = "retry"
    approve = "approve"
    abort = "abort"


class Resolution(StrictModel):
    """`retry` resumes from `resume_from`; `approve` re-runs the failed step with a
    one-step risky approval; `abort` ends the run with the original failure."""

    kind: ResolutionKind
    resume_from: str | None
    note: str

    @model_validator(mode="after")
    def _check(self) -> "Resolution":
        if (self.kind is ResolutionKind.retry) != (self.resume_from is not None):
            raise ValueError("resume_from is required for retry and must be null otherwise")
        return self


class InterventionRecord(StrictModel):
    request_id: str
    step_id: str
    failure_kind: FailureKind
    resolution: Resolution
    evidence_dir: str | None
