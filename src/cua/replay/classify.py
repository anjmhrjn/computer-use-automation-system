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


# Escalate when the environment blocked a correct artifact (a human can change the
# environment), never when the artifact, the system, or a guardrail stopped the run:
# an operator cannot fix drift, and an escalation path past `policy_denied` would be
# a bypass path. `permission_denied` escalates for a decision (abort, with a note),
# not a fix.
ESCALATES: frozenset[FailureKind] = frozenset(
    {
        FailureKind.session_expired,
        FailureKind.approval_required,
        FailureKind.interstitial_persisted,
        FailureKind.timeout,
        FailureKind.permission_denied,
    }
)
TERMINAL: frozenset[FailureKind] = frozenset(
    {
        FailureKind.target_drift,
        FailureKind.checkpoint_failed,
        FailureKind.surface_error,
        FailureKind.policy_denied,
    }
)


def escalates(kind: FailureKind) -> bool:
    if kind in ESCALATES:
        return True
    if kind in TERMINAL:
        return False
    raise TypeError(f"failure kind {kind.value!r} is not placed in the escalation table")


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
