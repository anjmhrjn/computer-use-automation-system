from __future__ import annotations

from pathlib import Path


class CompileError(ValueError):
    """`turn` is the transcript turn the problem is attributed to, when there is one."""

    def __init__(self, transcript: Path, turn: int | None, expected: str, observed: str) -> None:
        self.transcript = transcript
        self.turn = turn
        self.expected = expected
        self.observed = observed
        where = f"{transcript}" + (f" turn {turn}" if turn is not None else "")
        super().__init__(f"{where}: expected {expected}; observed {observed}")


class NotCompilable(CompileError):
    """The run did not end the way compile needs, or is missing what a step requires."""


class InlinedValue(CompileError):
    """A step or detector carries a `<param:…>` placeholder: a value the redactor
    replaced was inlined at discovery, and replay would send the placeholder literally."""


class Incompatible(CompileError):
    """A business-outcome run is not a prefix of the success run."""


class Indiscriminate(CompileError):
    """A business-outcome detector also holds on a page the success run went through."""
