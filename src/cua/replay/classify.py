"""Maps what went wrong to the error taxonomy. Pure: screening is a predicate over one
observation, and classification is a table over exception types. Anything not in
the table is not a replay failure and propagates."""

from __future__ import annotations

from cua.policy import ApprovalRequired, PolicyDenied, PolicyError
from cua.schema import AppProfile, Failure, FailureKind, Interstitial
from cua.surface import Observation, SurfaceError

from .errors import (
    CheckpointFailed,
    InterstitialDetected,
    PostconditionTimeout,
    ReplayError,
    TargetUnresolved,
)
from .predicates import holds
from .session import Session


def screen(observation: Observation, profile: AppProfile, session: Session) -> Interstitial | None:
    for interstitial in profile.interstitials:
        if holds(interstitial.detector, observation, session):
            return interstitial
    return None


def to_failure(exc: ReplayError | SurfaceError | PolicyError, step_id: str) -> Failure:
    if isinstance(exc, InterstitialDetected):
        kind = exc.interstitial.failure_kind
    elif isinstance(exc, PostconditionTimeout):
        kind = FailureKind.timeout
    elif isinstance(exc, TargetUnresolved):
        kind = FailureKind.target_drift
    elif isinstance(exc, CheckpointFailed):
        kind = FailureKind.checkpoint_failed
    elif isinstance(exc, PolicyDenied):
        kind = FailureKind.policy_denied
    elif isinstance(exc, ApprovalRequired):
        kind = FailureKind.approval_required
    elif isinstance(exc, SurfaceError):
        kind = FailureKind.surface_error
    else:
        raise TypeError(f"unclassified replay error {type(exc).__name__}")

    if isinstance(exc, ReplayError):
        return Failure(kind=kind, step_id=exc.step_id, expected=exc.expected, observed=exc.observed)
    if isinstance(exc, PolicyError):
        return Failure(kind=kind, step_id=step_id, expected=exc.expected, observed=exc.observed)
    return Failure(kind=kind, step_id=step_id, expected="surface action to complete", observed=str(exc))
