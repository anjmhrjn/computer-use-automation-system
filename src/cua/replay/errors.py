"""Typed replay failures. Item 4 only raises them; classification into business
outcome / recoverable / hard failure is item 5's job. Every error carries the step it
happened on, what was expected, and what was observed instead."""

from __future__ import annotations

from cua.surface import ResolutionError


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
