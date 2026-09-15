"""Typed replay failures. Every error carries the step it happened on, what was
expected, and what was observed instead. `replay()` catches all of them except
`MissingParameter`, which is a caller error raised before anything runs, and
reports them as a classified `Failure` on the result (see `classify.py`)."""

from __future__ import annotations

from cua.schema import Interstitial
from cua.surface import Observation, ResolutionError


class ReplayError(Exception):
    def __init__(self, step_id: str, expected: str, observed: str) -> None:
        self.step_id = step_id
        self.expected = expected
        self.observed = observed
        super().__init__(f"[{step_id}] expected {expected}; observed {observed}")


class MissingParameter(ReplayError):
    pass


class TargetUnresolved(ReplayError):
    def __init__(self, step_id: str, cause: ResolutionError) -> None:
        self.cause = cause
        super().__init__(
            step_id,
            f"exactly one {cause.descriptor.role!r} in frame {cause.descriptor.frame_path}",
            str(cause),
        )


class PostconditionTimeout(ReplayError):
    pass


class CheckpointFailed(ReplayError):
    pass


class InterstitialDetected(ReplayError):
    """An interstitial that could not be, or can no longer be, dismissed."""

    def __init__(
        self,
        step_id: str,
        interstitial: Interstitial,
        attempts: int,
        observation: Observation | None = None,
    ) -> None:
        self.interstitial = interstitial
        self.observation = observation
        observed = f"interstitial {interstitial.name!r}"
        if attempts:
            observed += f" still present after {attempts} dismissal(s)"
        super().__init__(step_id, "no interstitial", observed)
