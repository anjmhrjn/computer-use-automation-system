"""The two checks every action must pass before it is dispatched (invariants 4
and 12). Pure: a function of the action, the step's risk class, where the surface
is, and the policy. Nothing here observes or acts."""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase

from cua.schema import RiskClass
from cua.surface import Navigate, SurfaceAction

from .errors import ApprovalRequired, PolicyDenied


@dataclass(frozen=True)
class Policy:
    allowed_locations: tuple[str, ...]
    approve_risky: bool


def check(action: SurfaceAction, risk: RiskClass, location: str | None, policy: Policy) -> None:
    # A navigate is judged by where it goes; everything else by where the surface
    # already is. An action before any observation has no location to judge, and
    # that is a denial, not a pass.
    if isinstance(action, Navigate):
        where, what = action.location, "navigate to"
    else:
        where, what = location, f"{type(action).__name__.lower()} at"
    if where is None or not any(fnmatchcase(where, p) for p in policy.allowed_locations):
        raise PolicyDenied(
            f"a location matching one of {list(policy.allowed_locations)}",
            f"{what} {where!r}",
        )
    if risk is RiskClass.risky and not policy.approve_risky:
        raise ApprovalRequired(
            "risky step approved before dispatch (--approve-risky)",
            f"risk {risk.value!r}, not approved",
        )
