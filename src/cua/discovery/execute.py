"""Executes one model turn against the surface and proves it.

An `act` turn: the picked node becomes a descriptor that must resolve strictly
back to it; the action goes through `Session.act()`; the declared expectation is
polled with the same `holds()` replay uses. A `finish` turn: the detector must
hold on the page the model is looking at and every bound output must have been
read. Anything the model got wrong is a `DeadEnd` -- reported back to it, run
continues. Anything the run may not continue past is a `Halt`."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from cua import schema
from cua.policy import ApprovalRequired, PolicyDenied, Redactor
from cua.replay import InterstitialDetected, PostconditionTimeout, Session, holds, screened, summarize
from cua.surface import (
    Click,
    ElementNode,
    Navigate,
    Observation,
    ReadText,
    SelectOption,
    SurfaceAction,
    SurfaceError,
    SurfaceNotReady,
    TypeText,
)

from .derive import AmbiguousExpectation, DerivationFailed, describe, to_predicate
from .model import Exchange, Model
from .transcript import DiscoveryFailureKind, TranscriptWriter
from .turns import Act, Contract, Expect, ExpectElement, ExpectText, Finish, Turn

EXPECT_TIMEOUT_MS = 10_000


class _Problem(Exception):
    """`target` is the descriptor derived before the turn went wrong, if it got
    that far; a dead end keeps it so a fixture-mode run can re-bind the turn."""

    target: schema.TargetDescriptor | None = None

    def __init__(self, expected: str, observed: str) -> None:
        self.expected = expected
        self.observed = observed
        super().__init__(f"expected {expected}; observed {observed}")


class Halt(_Problem):
    """Ends the run at this turn with a classified failure."""

    def __init__(self, kind: DiscoveryFailureKind, expected: str, observed: str) -> None:
        self.kind = kind
        super().__init__(expected, observed)


class DeadEnd(_Problem):
    """The turn did not achieve what it claimed; the model is told and continues."""


@dataclass
class RunState:
    contract: Contract
    profile: schema.AppProfile
    model: Model
    session: Session
    redactor: Redactor
    writer: TranscriptWriter
    run_dir: Path
    outputs: dict[str, str] = field(default_factory=dict)
    history: list[Exchange] = field(default_factory=list)
    dead_ends: int = 0


def act(
    run: RunState, turn: Act, observation: Observation, snapshot: str, index: int
) -> tuple[schema.TargetDescriptor | None, schema.Predicate]:
    node, target = _pick(turn, observation, snapshot)
    try:
        return target, _perform(run, turn, node, observation, snapshot, index)
    except _Problem as exc:
        exc.target = target
        raise


def _pick(
    turn: Act, observation: Observation, snapshot: str
) -> tuple[ElementNode | None, schema.TargetDescriptor | None]:
    action = turn.action
    if isinstance(action, schema.Navigate):
        if turn.node_id is not None:
            raise DeadEnd("node_id null for navigate", f"node_id {turn.node_id!r}")
        return None, None
    if turn.node_id is None:
        raise DeadEnd(f"a node_id for {action.kind}", "null")
    node = observation.by_id(turn.node_id)
    if node is None:
        raise DeadEnd("a node_id from the current observation", f"unknown {turn.node_id!r}")
    try:
        return node, describe(observation, node, snapshot)
    except DerivationFailed as exc:
        raise DeadEnd("a node a descriptor can be derived for", str(exc)) from exc


def _perform(
    run: RunState,
    turn: Act,
    node: ElementNode | None,
    observation: Observation,
    snapshot: str,
    index: int,
) -> schema.Predicate:
    action = turn.action
    if isinstance(action, schema.ReadText):
        declared = {o.name for o in run.contract.outputs}
        if action.bind_to not in declared:
            raise DeadEnd(f"bind_to among {sorted(declared)}", repr(action.bind_to))

    try:
        surface_action = _to_surface_action(action, run.session)
    except KeyError as exc:
        raise DeadEnd("a declared input name", f"unknown parameter {exc}") from exc

    try:
        read = run.session.act(surface_action, node, turn.risk)
    except ApprovalRequired as exc:
        raise Halt(DiscoveryFailureKind.approval_required, exc.expected, exc.observed) from exc
    except PolicyDenied as exc:
        raise DeadEnd(exc.expected, exc.observed) from exc
    except SurfaceError as exc:
        raise DeadEnd("the action to be applicable", str(exc)) from exc
    if isinstance(action, schema.ReadText):
        run.outputs[action.bind_to] = read or ""
    _check_not_inlined(run, turn.expect)

    after_snapshot = f"ax/{index:03d}-after.json"
    try:
        after, predicate = _await(run, turn.expect, observation, after_snapshot)
    except PostconditionTimeout as exc:
        raise DeadEnd(exc.expected, exc.observed) from exc
    except InterstitialDetected as exc:
        raise Halt(DiscoveryFailureKind.interstitial, exc.expected, exc.observed) from exc
    except (AmbiguousExpectation, DerivationFailed) as exc:
        raise DeadEnd("an expectation naming one element", str(exc)) from exc
    snapshot_to(run, after_snapshot, after)
    return predicate


def _await(
    run: RunState, expect: Expect, before: Observation, snapshot: str
) -> tuple[Observation, schema.Predicate]:
    """Like `replay.await_predicate`, except the predicate itself may only become
    derivable once the expected page is there (an `element` expectation)."""
    deadline = time.monotonic() + EXPECT_TIMEOUT_MS / 1000
    summary = "surface never became ready"
    while True:
        try:
            after = screened(run.session.observe(), run.profile, run.session)
        except SurfaceNotReady:
            after = None
        if after is not None:
            predicate = to_predicate(expect, before, after, snapshot)
            if predicate is not None and holds(predicate, after, run.session):
                return after, predicate
            summary = summarize(after)
        if time.monotonic() >= deadline:
            if after is not None:
                summary = why_not(expect, after)
            raise PostconditionTimeout(run.session.step_id, describe_expect(expect), summary)


def finish(run: RunState, turn: Finish, observation: Observation, snapshot: str) -> schema.Predicate:
    declared = {o.name for o in run.contract.outputs}
    undeclared = sorted(set(turn.binds) - declared)
    if undeclared:
        raise DeadEnd(f"binds among {sorted(declared)}", f"undeclared {undeclared}")
    unread = sorted(set(turn.binds) - set(run.outputs))
    if unread:
        raise DeadEnd("every bound output read before finishing", f"{unread} never read")
    if turn.outcome_kind is schema.OutcomeKind.success:
        missing = sorted(declared - set(turn.binds))
        if missing:
            raise DeadEnd("a success outcome binding every declared output", f"{missing} not bound")
    _check_not_inlined(run, turn.detector)
    try:
        predicate = to_predicate(turn.detector, observation, observation, snapshot)
    except (AmbiguousExpectation, DerivationFailed) as exc:
        raise DeadEnd("a detector naming one element", str(exc)) from exc
    if predicate is None or not holds(predicate, observation, run.session):
        raise DeadEnd(
            f"detector {describe_expect(turn.detector)} to hold on the current page",
            why_not(turn.detector, observation),
        )
    return predicate


def why_not(expect: Expect, observation: Observation) -> str:
    """What the model needs in order to correct an expectation that did not hold:
    for an element, the nodes of that role it could have named."""
    if not isinstance(expect, ExpectElement):
        return summarize(observation)
    same_role = [
        n for n in observation.nodes if n.role == expect.role and n.frame_path == expect.frame_path
    ]
    if not same_role:
        return f"{summarize(observation)}; no {expect.role!r} in frame {expect.frame_path}"
    listed = "; ".join(
        f"name={n.name!r} label={n.label!r} text={n.text[:60]!r}" for n in same_role[:5]
    )
    return f"{len(same_role)} {expect.role!r} node(s) in frame {expect.frame_path}, none matching: {listed}"


MAX_REPEATS = 3


def repeated(turn: Turn, run: RunState) -> int:
    """How many immediately preceding turns were this same turn, each a dead end.
    Re-issuing a failed turn unchanged cannot succeed, so it is refused before it
    runs; the third refusal ends the run."""
    text = turn.model_dump_json()
    count = 0
    for exchange in reversed(run.history):
        if exchange.response != run.redactor.redact(text) or exchange.result == "ok":
            break
        count += 1
    return count


def _check_not_inlined(run: RunState, expect: Expect) -> None:
    """A predicate that quotes the value just read or typed is true for this
    run's inputs only; the artifact it would compile into could never replay for
    another member (invariant 7 for outputs, in spirit)."""
    if not isinstance(expect, ExpectText):
        return
    if "<param:" in expect.text:
        raise DeadEnd(
            "text that is a fixed part of the application (quote only the fragment "
            "before or after the input value)",
            "text expectation contains a redacted input placeholder, which the page never shows",
        )
    wanted = expect.text.strip().casefold()
    values = {name: v for name, v in run.outputs.items()} | {
        i.name: i.value for i in run.contract.inputs
    }
    for name, value in values.items():
        if value and value.strip().casefold() == wanted:
            raise DeadEnd(
                "an expectation that holds for any input (name the element by role and label/name)",
                f"text expectation quotes the value of {name!r}",
            )


def snapshot_to(run: RunState, name: str, observation: Observation) -> str:
    path = run.run_dir / name
    path.write_text(run.redactor.redact(observation.model_dump_json(indent=1)) + "\n")
    return name


def describe_expect(expect: Expect) -> str:
    # Parameter values are named, never inlined (invariant 7).
    return expect.model_dump_json()


def _to_surface_action(action: schema.Action, session: Session) -> SurfaceAction:
    if isinstance(action, schema.Navigate):
        return Navigate(action.location)
    if isinstance(action, schema.Click):
        return Click()
    if isinstance(action, schema.TypeText):
        return TypeText(session.value(action.value))
    if isinstance(action, schema.SelectOption):
        return SelectOption(session.value(action.option))
    if isinstance(action, schema.ReadText):
        return ReadText()
    raise TypeError(f"unknown action {type(action).__name__}")
