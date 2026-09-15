"""A `Model` that answers from a recorded transcript. Everything else in the run
-- surface, policy, derivation, verification, evidence -- is real; only the
provider is replaced, so a fixture-mode run needs no API key and still proves the
recorded turns work against the live app.

A sensitive input's value is `<param:name>` on disk (the redactor wrote it that
way), so the caller supplies the real values; a run without them cannot proceed
and says so.

Node ids are opaque and scoped to one observation, so a recorded `node_id` is
meaningless in a new run. Each recorded turn is re-bound through the descriptor
the loop derived for it, resolved against the live observation via
`Prompt.locate` -- the same identity replay will use. The recorded location must
match the live one and the descriptor must resolve; either divergence is a hard
failure, not a best-effort continue."""

from __future__ import annotations

from cua.schema import TargetDescriptor, ValueEquals
from cua.surface import ResolutionError

from .model import ModelError, Prompt
from .transcript import ContractDeclared, Entry, RunStarted, TurnRecord
from .turns import Act, Contract, ExpectValue, Turn


class TranscriptExhausted(ModelError):
    pass


class TranscriptDiverged(ModelError):
    def __init__(self, turn: int, expected: str, observed: str) -> None:
        self.turn = turn
        self.expected = expected
        self.observed = observed
        super().__init__(f"turn {turn}: recorded at {expected!r}, live surface at {observed!r}")


class TranscriptModel:
    def __init__(self, entries: list[Entry], params: dict[str, str]) -> None:
        started = [e for e in entries if isinstance(e, RunStarted)]
        contracts = [e for e in entries if isinstance(e, ContractDeclared)]
        if len(started) != 1 or len(contracts) != 1:
            raise ModelError("transcript must contain exactly one run_started and one contract_declared")
        self.started = started[0]
        self._contract = _with_values(contracts[0].contract, params)
        self._turns = [e for e in entries if isinstance(e, TurnRecord)]
        self._cursor = 0

    def declare(self, goal: str) -> Contract:
        return self._contract

    def next_turn(self, prompt: Prompt) -> Turn:
        if self._cursor >= len(self._turns):
            raise TranscriptExhausted(f"transcript has {len(self._turns)} turns, run asked for more")
        recorded = self._turns[self._cursor]
        if recorded.location != prompt.location:
            raise TranscriptDiverged(recorded.index, recorded.location, prompt.location)
        self._cursor += 1
        return _rebind(recorded, prompt)


def _rebind(recorded: TurnRecord, prompt: Prompt) -> Turn:
    turn = recorded.response
    if not isinstance(turn, Act) or turn.node_id is None or recorded.target is None:
        return turn
    node_id = _locate(recorded, prompt, recorded.target)
    expect = turn.expect
    if isinstance(expect, ExpectValue):
        if expect.node_id == turn.node_id:
            expect = expect.model_copy(update={"node_id": node_id})
        elif isinstance(recorded.postcondition, ValueEquals):
            expect = expect.model_copy(
                update={"node_id": _locate(recorded, prompt, recorded.postcondition.target)}
            )
    return turn.model_copy(update={"node_id": node_id, "expect": expect})


def _locate(recorded: TurnRecord, prompt: Prompt, descriptor: TargetDescriptor) -> str:
    try:
        return prompt.locate(descriptor)
    except ResolutionError as exc:
        raise TranscriptDiverged(
            recorded.index, f"{descriptor.role} {descriptor.accessible_name or descriptor.label!r}", str(exc)
        ) from exc


def _with_values(contract: Contract, params: dict[str, str]) -> Contract:
    unknown = sorted(set(params) - {i.name for i in contract.inputs})
    if unknown:
        raise ModelError(f"unknown parameters {unknown}; transcript declares {[i.name for i in contract.inputs]}")
    inputs = []
    for spec in contract.inputs:
        value = params.get(spec.name, spec.value)
        if value == f"<param:{spec.name}>":
            raise ModelError(f"input {spec.name!r} was redacted in the transcript; pass --param {spec.name}=...")
        inputs.append(spec.model_copy(update={"value": value}))
    return contract.model_copy(update={"inputs": inputs})
