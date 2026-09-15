"""A policy refusal at the chokepoint. Carries what was expected and what was
observed; the step it happened on is attached by the engine's classifier, which
is the only party that knows it."""

from __future__ import annotations


class PolicyError(Exception):
    def __init__(self, expected: str, observed: str) -> None:
        self.expected = expected
        self.observed = observed
        super().__init__(f"expected {expected}; observed {observed}")


class PolicyDenied(PolicyError):
    """The action would happen at, or lead to, a location outside the allowlist."""


class ApprovalRequired(PolicyError):
    """A risky-class step reached the chokepoint without approval (invariant 12)."""
