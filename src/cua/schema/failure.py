from __future__ import annotations

from enum import Enum

from .common import StrictModel


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
