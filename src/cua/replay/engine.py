"""Executes a Capability with supplied parameters. No model anywhere in this loop
(invariant 5): every decision is a predicate over an observation.

Per step: observe, resolve the target, act through the session chokepoint, then poll
observations until the postcondition holds or the step's timeout elapses. Every
observation the engine takes is screened against the app profile's interstitials
first, because an interstitial is exactly the page that keeps a postcondition from
ever holding. A dismissable one is dismissed and the step re-run from its own
observe, up to a ceiling. Business outcome detectors are evaluated only on the
observation that satisfied the postcondition, so a detector can never fire on the
page from before the action. The success outcome is checked only after the last
step and the checkpoint, because it binds outputs that do not exist earlier.

Failures never raise out of `replay()`; they come back classified on the result.
The one exception is `MissingParameter`, a caller error raised before the run.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from cua import schema
from cua.schema import (
    AppProfile,
    Capability,
    Failure,
    OutcomeKind,
    OutcomeSpec,
    Recovery,
    ReplayResult,
    ReplayStatus,
    Step,
    StepTrace,
)
from cua.surface import (
    Click,
    Navigate,
    Observation,
    ReadText,
    ResolutionError,
    SelectOption,
    Surface,
    SurfaceAction,
    SurfaceError,
    SurfaceNotReady,
    TypeText,
    resolve,
)

from .classify import screen, to_failure
from .errors import (
    CheckpointFailed,
    InterstitialDetected,
    MissingParameter,
    PostconditionTimeout,
    ReplayError,
    TargetUnresolved,
)
from .predicates import describe, holds
from .session import Session

MAX_DISMISSALS = 2


@dataclass
class _Run:
    capability: Capability
    profile: AppProfile
    session: Session
    outputs: dict[str, str] = field(default_factory=dict)
    traces: list[StepTrace] = field(default_factory=list)
    step_id: str = "<start>"


def replay(
    capability: Capability, params: dict[str, str], surface: Surface, profile: AppProfile
) -> ReplayResult:
    _check_params(capability, params)
    if profile.app_id != capability.target.app_id:
        raise ValueError(
            f"profile is for {profile.app_id!r}, capability targets {capability.target.app_id!r}"
        )
    run = _Run(capability, profile, Session(surface, params))
    try:
        return _execute(run)
    except (ReplayError, SurfaceError) as exc:
        return _result(run, None, to_failure(exc, run.step_id))


def _execute(run: _Run) -> ReplayResult:
    capability = run.capability
    business = [o for o in capability.outcomes if o.kind is OutcomeKind.business]
    success = next(o for o in capability.outcomes if o.kind is OutcomeKind.success)

    for step in capability.steps:
        run.step_id = step.step_id
        observation = _run_step(step, run)
        for outcome in business:
            if holds(outcome.detector, observation, run.session):
                return _result(run, outcome, None)

    observation = run.session.observe()
    blocking = screen(observation, run.profile, run.session)
    if blocking is not None:
        raise InterstitialDetected(run.step_id, blocking, 0)
    if not holds(capability.checkpoint, observation, run.session):
        raise CheckpointFailed(run.step_id, describe(capability.checkpoint), _summary(observation))
    if not holds(success.detector, observation, run.session):
        raise CheckpointFailed(run.step_id, describe(success.detector), _summary(observation))
    return _result(run, success, None)


def _run_step(step: Step, run: _Run) -> Observation:
    """One step, re-run after each dismissed interstitial. Returns the observation
    that satisfied the postcondition."""
    started = time.monotonic()
    recoveries: list[Recovery] = []
    while True:
        try:
            observation, tier = _attempt(step, run, len(recoveries))
        except InterstitialDetected as blocked:
            if blocked.interstitial.dismiss is None or len(recoveries) >= MAX_DISMISSALS:
                raise
            _dismiss(step, blocked, run)
            recoveries.append(
                Recovery(interstitial=blocked.interstitial.name, attempt=len(recoveries) + 1)
            )
            continue
        run.traces.append(
            StepTrace(
                step_id=step.step_id,
                resolved_tier=tier,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                recoveries=recoveries,
            )
        )
        return observation


def _attempt(step: Step, run: _Run, attempts: int) -> tuple[Observation, str | None]:
    session = run.session
    observation = _screened(session.observe(), step, run, attempts)
    node = None
    tier = None
    if step.target is not None:
        try:
            resolution = resolve(observation, step.target)
        except ResolutionError as exc:
            raise TargetUnresolved(step.step_id, exc) from exc
        node, tier = resolution.node, resolution.tier

    read = session.act(_to_surface_action(step, session), node)
    if isinstance(step.action, schema.ReadText):
        run.outputs[step.action.bind_to] = read or ""

    return _await_postcondition(step, run, attempts), tier


def _screened(observation: Observation, step: Step, run: _Run, attempts: int) -> Observation:
    blocking = screen(observation, run.profile, run.session)
    if blocking is not None:
        raise InterstitialDetected(step.step_id, blocking, attempts, observation)
    return observation


def _dismiss(step: Step, blocked: InterstitialDetected, run: _Run) -> None:
    dismiss = blocked.interstitial.dismiss
    assert dismiss is not None and blocked.observation is not None
    try:
        resolution = resolve(blocked.observation, dismiss)
    except ResolutionError as exc:
        raise TargetUnresolved(step.step_id, exc) from exc
    run.session.act(Click(), resolution.node)


def _await_postcondition(step: Step, run: _Run, attempts: int) -> Observation:
    # No fixed delay: each iteration is a fresh observation, and observe() itself
    # blocks on the surface's own readiness (invariant 2). A surface that is not
    # ready yet is "not yet", and the step deadline decides when it is "never".
    deadline = time.monotonic() + step.postcondition_timeout_ms / 1000
    summary = "surface never became ready"
    while True:
        try:
            observation = _screened(run.session.observe(), step, run, attempts)
        except SurfaceNotReady:
            observation = None
        if observation is not None:
            if holds(step.postcondition, observation, run.session):
                return observation
            summary = _summary(observation)
        if time.monotonic() >= deadline:
            raise PostconditionTimeout(step.step_id, describe(step.postcondition), summary)


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


def _summary(observation: Observation) -> str:
    return f"location {observation.location!r}, {len(observation.nodes)} nodes"


def _result(run: _Run, outcome: OutcomeSpec | None, failure: Failure | None) -> ReplayResult:
    capability = run.capability
    if outcome is None:
        assert failure is not None
        return ReplayResult(
            capability_id=capability.capability_id,
            version=capability.version,
            status=ReplayStatus.failed,
            outcome=None,
            outputs={},
            steps=run.traces,
            failure=failure,
        )
    unread = [name for name in outcome.binds if name not in run.outputs]
    if unread:
        raise CheckpointFailed(
            run.step_id,
            f"outputs {outcome.binds} read before outcome {outcome.name!r}",
            f"{unread} never read",
        )
    status = (
        ReplayStatus.success if outcome.kind is OutcomeKind.success else ReplayStatus.business_outcome
    )
    return ReplayResult(
        capability_id=capability.capability_id,
        version=capability.version,
        status=status,
        outcome=outcome.name,
        outputs={name: run.outputs[name] for name in outcome.binds},
        steps=run.traces,
        failure=None,
    )
