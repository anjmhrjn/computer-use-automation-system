"""The raw record of a discovery run, one JSON line per entry, dead ends included.
Not a deliverable and never replayed as-is: item 8 compiles it, and
`TranscriptModel` re-serves its model responses for fixture-mode runs.

Every line passes the run's redactor on the way to disk, the same rule as the
event log (invariant 6). The reader is strict: a line that does not parse as a
known entry is an error naming the line."""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Annotated, Literal, TextIO, Union

from pydantic import Field, TypeAdapter, ValidationError

from cua.policy import Redactor
from cua.schema import OutcomeKind, Predicate, StrictModel, TargetDescriptor

from .turns import Contract, Turn


class DiscoveryStatus(str, Enum):
    success = "success"
    business_outcome = "business_outcome"
    failed = "failed"


class DiscoveryFailureKind(str, Enum):
    gave_up = "gave_up"
    stuck = "stuck"
    budget_exhausted = "budget_exhausted"
    approval_required = "approval_required"
    interstitial = "interstitial"
    model_error = "model_error"
    surface_error = "surface_error"


class DiscoveryFailure(StrictModel):
    kind: DiscoveryFailureKind
    turn: int
    expected: str
    observed: str


class RunStarted(StrictModel):
    event: Literal["run_started"]
    run_id: str
    goal: str
    target: str
    app_id: str
    started_at: str


class ContractDeclared(StrictModel):
    event: Literal["contract_declared"]
    contract: Contract


class TurnRecord(StrictModel):
    """`ok` false is a dead end: the action ran (or was refused) but the
    expectation did not hold, and the model was told so."""

    event: Literal["turn"]
    index: int
    location: str
    ax_snapshot: str
    response: Turn
    target: TargetDescriptor | None
    postcondition: Predicate | None
    ok: bool
    expected: str | None
    observed: str | None
    elapsed_ms: int


class RunFinished(StrictModel):
    event: Literal["run_finished"]
    status: DiscoveryStatus
    outcome_name: str | None
    outcome_kind: OutcomeKind | None
    outputs: dict[str, str]
    failure: DiscoveryFailure | None


Entry = Annotated[
    Union[RunStarted, ContractDeclared, TurnRecord, RunFinished],
    Field(discriminator="event"),
]

_ENTRY = TypeAdapter(Entry)


class TranscriptWriter:
    def __init__(self, stream: TextIO, redactor: Redactor) -> None:
        self._stream = stream
        self._redactor = redactor

    def write(self, entry: Entry) -> None:
        self._stream.write(self._redactor.redact(entry.model_dump_json()) + "\n")
        self._stream.flush()


class MalformedTranscript(ValueError):
    def __init__(self, path: Path, line: int, reason: str) -> None:
        self.path = path
        self.line = line
        super().__init__(f"{path}:{line}: {reason}")


def read(path: Path) -> list[Entry]:
    entries: list[Entry] = []
    for number, raw in enumerate(path.read_text().splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            entries.append(_ENTRY.validate_json(raw))
        except (ValidationError, json.JSONDecodeError) as exc:
            raise MalformedTranscript(path, number, str(exc).splitlines()[0]) from exc
    return entries
