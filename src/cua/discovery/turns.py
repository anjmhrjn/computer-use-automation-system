"""What the model emits, one structured output per call. Two schemas: `Contract`
for the first call (declared from the goal text alone) and `Turn` for every call
after it.

The model never authors a `TargetDescriptor`. It picks a `node_id` from the
rendered observation and states its expectation in the reduced `Expect` form; the
loop derives the descriptor and the full `Predicate` by rules (`derive.py`). The
action itself reuses the artifact's `Action` types, so a typed value is a
`ValueRef` -- the model types parameters by name, never by value."""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import Field

from cua.schema import (
    Action,
    NameMatch,
    OutcomeKind,
    OutputSpec,
    ParameterSpec,
    RiskClass,
    StrictModel,
    ValueRef,
)


class DeclaredInput(ParameterSpec):
    """`value` is what the model read from the goal text. It is the only place a
    raw input value is ever emitted by the model, and it is stripped from the
    transcript by the redactor the loop builds from it."""

    value: str


class Contract(StrictModel):
    name: str
    description: str
    inputs: list[DeclaredInput]
    outputs: list[OutputSpec]


class ExpectLocation(StrictModel):
    kind: Literal["location"]
    pattern: str


class ExpectText(StrictModel):
    kind: Literal["text"]
    text: str
    frame_path: list[str]


class ExpectElement(StrictModel):
    """An element on the page *after* the action, named by role plus accessible
    name and/or label; the loop derives the rest once the page is there."""

    kind: Literal["element"]
    role: str
    accessible_name: str | None
    name_match: NameMatch
    label: str | None
    frame_path: list[str]


class ExpectValue(StrictModel):
    """A control on the *current* page holds a value after the action."""

    kind: Literal["value"]
    node_id: str
    expected: ValueRef


Expect = Annotated[
    Union[ExpectLocation, ExpectText, ExpectElement, ExpectValue],
    Field(discriminator="kind"),
]


class Act(StrictModel):
    kind: Literal["act"]
    intent: str
    action: Action
    node_id: str | None
    risk: RiskClass
    expect: Expect


class Finish(StrictModel):
    kind: Literal["finish"]
    outcome_name: str
    outcome_kind: OutcomeKind
    description: str
    detector: Expect
    binds: list[str]


class GiveUp(StrictModel):
    kind: Literal["give_up"]
    reason: str


Turn = Annotated[Union[Act, Finish, GiveUp], Field(discriminator="kind")]


class TurnEnvelope(StrictModel):
    """Structured outputs need an object at the root; a bare union is not one."""

    turn: Turn
