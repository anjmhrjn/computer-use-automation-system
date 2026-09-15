"""Executes a Capability with supplied parameters. No model anywhere in this loop
(invariant 5): every decision is a predicate over an observation.

Per step: observe, resolve the target, act through the session chokepoint, then poll
observations until the postcondition holds or the step's timeout elapses. Business
outcome detectors are evaluated on the observation that satisfied the postcondition,
so a detector can never fire on the page from before the action. The success outcome
is checked only after the last step and the checkpoint, because it binds outputs that
do not exist earlier.
"""

from __future__ import annotations

import time

from cua import schema
from cua.schema import Capability, OutcomeKind, OutcomeSpec, ReplayResult, Step, StepTrace
from cua.surface import (
    Click,
    Navigate,
    Observation,
    ReadText,
    ResolutionError,
    SelectOption,
    Surface,
    SurfaceAction,
    TypeText,
    resolve,
)

from .errors import CheckpointFailed, MissingParameter, PostconditionTimeout, TargetUnresolved
from .predicates import describe, holds
from .session import Session


def replay(capability: Capability, params: dict[str, str], surface: Surface) -> ReplayResult:
    _check_params(capability, params)
    session = Session(surface, params)
    outputs: dict[str, str] = {}
    traces: list[StepTrace] = []
    business = [o for o in capability.outcomes if o.kind is OutcomeKind.business]
    success = next(o for o in capability.outcomes if o.kind is OutcomeKind.success)

    for step in capability.steps:
        started = time.monotonic()
        observation = session.observe()
        node = None
        tier = None
        if step.target is not None:
            try:
                resolution = resolve(observation, step.target)
            except ResolutionError as exc:
                raise TargetUnresolved(step.step_id, exc) from exc
            node, tier = resolution.node, resolution.tier

        action = _to_surface_action(step, session)
        read = session.act(action, node)
        if isinstance(step.action, schema.ReadText):
            outputs[step.action.bind_to] = read or ""

        observation = _await_postcondition(step, session)
        traces.append(
            StepTrace(
                step_id=step.step_id,
                resolved_tier=tier,
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )
        )
        for outcome in business:
            if holds(outcome.detector, observation, session):
                return _result(capability, outcome, outputs, traces)

    observation = session.observe()
    last = capability.steps[-1].step_id
    if not holds(capability.checkpoint, observation, session):
        raise CheckpointFailed(last, describe(capability.checkpoint), _summary(observation))
    if not holds(success.detector, observation, session):
        raise CheckpointFailed(last, describe(success.detector), _summary(observation))
    return _result(capability, success, outputs, traces)


def _check_params(capability: Capability, params: dict[str, str]) -> None:
    declared = {p.name for p in capability.inputs}
    unknown = sorted(set(params) - declared)
    if unknown:
        raise MissingParameter("<params>", f"inputs among {sorted(declared)}", f"unknown {unknown}")
    missing = sorted(p.name for p in capability.inputs if p.required and p.name not in params)
    if missing:
        raise MissingParameter("<params>", f"required inputs {missing}", "not supplied")


def _to_surface_action(step: Step, session: Session) -> SurfaceAction:
    action = step.action
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


def _await_postcondition(step: Step, session: Session) -> Observation:
    # No fixed delay: each iteration is a fresh observation, and observe() itself
    # blocks on the surface's own readiness (invariant 2).
    deadline = time.monotonic() + step.postcondition_timeout_ms / 1000
    while True:
        observation = session.observe()
        if holds(step.postcondition, observation, session):
            return observation
        if time.monotonic() >= deadline:
            raise PostconditionTimeout(
                step.step_id, describe(step.postcondition), _summary(observation)
            )


def _summary(observation: Observation) -> str:
    return f"location {observation.location!r}, {len(observation.nodes)} nodes"


def _result(
    capability: Capability,
    outcome: OutcomeSpec,
    outputs: dict[str, str],
    traces: list[StepTrace],
) -> ReplayResult:
    unread = [name for name in outcome.binds if name not in outputs]
    if unread:
        raise CheckpointFailed(
            traces[-1].step_id,
            f"outputs {outcome.binds} read before outcome {outcome.name!r}",
            f"{unread} never read",
        )
    return ReplayResult(
        capability_id=capability.capability_id,
        version=capability.version,
        outcome=outcome.name,
        outputs={name: outputs[name] for name in outcome.binds},
        steps=traces,
    )
