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
Every string that leaves as a failure or an event passes the run's redactor
(invariant 6); outputs do not, they are the answer the caller asked for.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TextIO

from cua import schema
from cua.policy import Policy, PolicyError, Redactor
from cua.schema import (
    AppProfile,
    Capability,
    Failure,
    OutcomeKind,
    OutcomeSpec,
    Recovery,
    ReplayResult,
    ReplayStatus,
    RiskClass,
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
    TypeText,
    resolve,
)

from .classify import screen, to_failure
from .errors import (
    CheckpointFailed,
    InterstitialDetected,
    MissingParameter,
    ReplayError,
    TargetUnresolved,
)
from .events import EventLog
from .predicates import describe, holds
from .session import Session
from .wait import await_predicate, screened, summarize

MAX_DISMISSALS = 2


@dataclass
class _Run:
    capability: Capability
    profile: AppProfile
    session: Session
    outputs: dict[str, str] = field(default_factory=dict)
    traces: list[StepTrace] = field(default_factory=list)

    @property
    def step_id(self) -> str:
        return self.session.step_id


def replay(
    capability: Capability,
    params: dict[str, str],
    surface: Surface,
    profile: AppProfile,
    *,
    approve_risky: bool = False,
    log_stream: TextIO | None = None,
) -> ReplayResult:
    _check_params(capability, params)
    if profile.app_id != capability.target.app_id:
        raise ValueError(
            f"profile is for {profile.app_id!r}, capability targets {capability.target.app_id!r}"
        )
    log = EventLog(log_stream, Redactor.for_run(capability, params))
    policy = Policy(tuple(profile.allowed_locations), approve_risky)
    run = _Run(capability, profile, Session(surface, params, policy, log))
    try:
        return _execute(run)
    except (ReplayError, SurfaceError, PolicyError) as exc:
        return _result(run, None, to_failure(exc, run.step_id))


def _execute(run: _Run) -> ReplayResult:
    capability = run.capability
    business = [o for o in capability.outcomes if o.kind is OutcomeKind.business]
    success = next(o for o in capability.outcomes if o.kind is OutcomeKind.success)

    for step in capability.steps:
        run.session.step_id = step.step_id
        observation = _run_step(step, run)
        for outcome in business:
            if holds(outcome.detector, observation, run.session):
                return _result(run, outcome, None)

    observation = run.session.observe()
    blocking = screen(observation, run.profile, run.session)
    if blocking is not None:
        raise InterstitialDetected(run.step_id, blocking, 0)
    if not holds(capability.checkpoint, observation, run.session):
        raise CheckpointFailed(run.step_id, describe(capability.checkpoint), summarize(observation))
    if not holds(success.detector, observation, run.session):
        raise CheckpointFailed(run.step_id, describe(success.detector), summarize(observation))
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
        trace = StepTrace(
            step_id=step.step_id,
            resolved_tier=tier,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            recoveries=recoveries,
        )
        run.traces.append(trace)
        run.session.log.emit("step_completed", **trace.model_dump())
        return observation


def _attempt(step: Step, run: _Run, attempts: int) -> tuple[Observation, str | None]:
    session = run.session
    observation = screened(session.observe(), run.profile, session, attempts)
    node = None
    tier = None
    if step.target is not None:
        try:
            resolution = resolve(observation, step.target)
        except ResolutionError as exc:
            raise TargetUnresolved(step.step_id, exc) from exc
        node, tier = resolution.node, resolution.tier

    read = session.act(_to_surface_action(step, session), node, step.risk)
    if isinstance(step.action, schema.ReadText):
        run.outputs[step.action.bind_to] = read or ""

    observation = await_predicate(
        step.postcondition, step.postcondition_timeout_ms, run.profile, session, attempts
    )
    return observation, tier


def _dismiss(step: Step, blocked: InterstitialDetected, run: _Run) -> None:
    dismiss = blocked.interstitial.dismiss
    assert dismiss is not None and blocked.observation is not None
    try:
        resolution = resolve(blocked.observation, dismiss)
    except ResolutionError as exc:
        raise TargetUnresolved(step.step_id, exc) from exc
    # Dismissing a notice is the profile's own control, not the step's action.
    run.session.act(Click(), resolution.node, RiskClass.reversible)
    run.session.log.emit(
        "interstitial_dismissed", step_id=step.step_id, interstitial=blocked.interstitial.name
    )


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


def _result(run: _Run, outcome: OutcomeSpec | None, failure: Failure | None) -> ReplayResult:
    capability = run.capability
    log = run.session.log
    if outcome is None:
        assert failure is not None
        redact = log.redactor.redact
        failure = failure.model_copy(
            update={"expected": redact(failure.expected), "observed": redact(failure.observed)}
        )
        log.emit("run_finished", status="failed", outcome=None, failure_kind=failure.kind.value)
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
    log.emit("run_finished", status=status.value, outcome=outcome.name, failure_kind=None)
    return ReplayResult(
        capability_id=capability.capability_id,
        version=capability.version,
        status=status,
        outcome=outcome.name,
        outputs={name: run.outputs[name] for name in outcome.binds},
        steps=run.traces,
        failure=None,
    )
