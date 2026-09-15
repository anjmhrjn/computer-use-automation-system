"""Ordered steps from a run's turns: every ok `act` turn, in order, and nothing
else. Dead ends are the turns the model was told did not work; a step is only
ever something that was proven to.

The descriptor and postcondition on each record were derived and verified at
discovery, so they are carried over as recorded, evidence references included --
the provenance names the run they point into."""

from __future__ import annotations

import re

from cua.discovery import EXPECT_TIMEOUT_MS, Act, TurnRecord
from cua.schema import (
    Action,
    ElementAbsent,
    ElementPresent,
    Navigate,
    ParameterValue,
    Predicate,
    ReadText,
    SelectOption,
    Step,
    TargetDescriptor,
    TypeText,
    ValueEquals,
)

from .errors import InlinedValue, NotCompilable
from .run import DiscoveryRun

PLACEHOLDER = "<param:"


def compile_steps(run: DiscoveryRun) -> list[Step]:
    steps: list[Step] = []
    used: set[str] = set()
    for record in kept_acts(run):
        turn = record.response
        assert isinstance(turn, Act)
        if record.postcondition is None:
            raise NotCompilable(
                run.path, record.index, "a postcondition on every ok act turn", "none recorded"
            )
        if PLACEHOLDER in turn.action.model_dump_json() or (
            record.target is not None and PLACEHOLDER in record.target.model_dump_json()
        ) or PLACEHOLDER in record.postcondition.model_dump_json():
            raise InlinedValue(
                run.path,
                record.index,
                "parameters referenced by name",
                "a redacted input value inlined into the action, target or postcondition",
            )
        step_id = unique(step_name(turn.action, record.target), used)
        steps.append(
            Step(
                step_id=step_id,
                intent=turn.intent,
                action=turn.action,
                target=record.target,
                risk=turn.risk,
                postcondition=record.postcondition,
                postcondition_timeout_ms=EXPECT_TIMEOUT_MS,
            )
        )
    if not steps:
        raise NotCompilable(run.path, None, "at least one ok act turn", "none")
    return steps


def kept_acts(run: DiscoveryRun) -> list[TurnRecord]:
    return [r for r in run.turns if r.ok and isinstance(r.response, Act)]


def step_name(action: Action, target: TargetDescriptor | None) -> str:
    named = slug(target.accessible_name or target.label or "") if target else ""
    if isinstance(action, Navigate):
        return f"navigate_{slug(action.location) or 'location'}"
    if isinstance(action, ReadText):
        return f"read_{slug(action.bind_to)}"
    if isinstance(action, TypeText):
        value = action.value
        return f"enter_{value.name if isinstance(value, ParameterValue) else named or 'text'}"
    if isinstance(action, SelectOption):
        option = action.option
        return f"select_{option.name if isinstance(option, ParameterValue) else named or 'option'}"
    return f"click_{named or 'control'}"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def unique(name: str, used: set[str]) -> str:
    candidate = name
    n = 1
    while candidate in used:
        n += 1
        candidate = f"{name}_{n}"
    used.add(candidate)
    return candidate


def without_evidence(predicate: Predicate) -> Predicate:
    """For comparing what two runs recorded: the evidence path differs per run and
    is not part of what the descriptor identifies."""
    if isinstance(predicate, (ElementPresent, ElementAbsent, ValueEquals)):
        return predicate.model_copy(update={"target": bare(predicate.target)})
    return predicate


def bare(target: TargetDescriptor | None) -> TargetDescriptor | None:
    return None if target is None else target.model_copy(update={"evidence": None})
