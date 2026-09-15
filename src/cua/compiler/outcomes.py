"""Outcomes from `finish` turns. The success run supplies the success outcome
and the steps; each business-outcome run supplies one business outcome and must
be a prefix of the success run, so that replaying the success steps walks the
page the business detector was observed on.

A business detector is evaluated against every page the success run settled on
(the observation before each following turn); one that holds there would end a
replay early with a wrong answer, so it is rejected here rather than at replay."""

from __future__ import annotations

from cua.discovery import Act, DiscoveryStatus, Finish, TurnRecord
from cua.replay import holds
from cua.schema import OutcomeKind, OutcomeSpec, Step, TargetDescriptor, ValueEquals

from .errors import Incompatible, Indiscriminate, InlinedValue, NotCompilable
from .run import DiscoveryRun
from .steps import PLACEHOLDER, bare, kept_acts, without_evidence


def success_outcome(run: DiscoveryRun) -> OutcomeSpec:
    if run.finished.status is not DiscoveryStatus.success:
        raise NotCompilable(
            run.path, None, "a run that finished with the success outcome", run.finished.status.value
        )
    return _outcome(run, OutcomeKind.success)


def business_outcome(extra: DiscoveryRun, primary: DiscoveryRun, steps: list[Step]) -> OutcomeSpec:
    if extra.finished.status is not DiscoveryStatus.business_outcome:
        raise NotCompilable(
            extra.path, None, "a run that finished with a business outcome", extra.finished.status.value
        )
    _check_prefix(extra, steps)
    outcome = _outcome(extra, OutcomeKind.business)
    for name in _settled_pages(primary):
        if holds(outcome.detector, primary.snapshot(name), None):
            raise Indiscriminate(
                extra.path,
                _finish_record(extra).index,
                f"detector for {outcome.name!r} to hold only on its own page",
                f"it also holds on {primary.path.parent / name}",
            )
    return outcome


def _outcome(run: DiscoveryRun, kind: OutcomeKind) -> OutcomeSpec:
    record = _finish_record(run)
    turn = record.response
    assert isinstance(turn, Finish)
    detector = record.postcondition
    if detector is None:
        raise NotCompilable(run.path, record.index, "a derived detector on the finish turn", "none recorded")
    if PLACEHOLDER in detector.model_dump_json():
        raise InlinedValue(
            run.path, record.index, "a detector free of input values", "a redacted input value inlined"
        )
    if isinstance(detector, ValueEquals):
        raise NotCompilable(
            run.path,
            record.index,
            "an outcome detector decidable from the page alone (element or text)",
            "value_equals, whose parameter value is not known at compile time",
        )
    if not holds(detector, run.snapshot(record.ax_snapshot), None):
        raise NotCompilable(
            run.path, record.index, "the detector to hold on the page it was declared on", "it does not"
        )
    return OutcomeSpec(
        name=turn.outcome_name,
        kind=kind,
        detector=detector,
        binds=list(turn.binds),
        description=turn.description,
    )


def _finish_record(run: DiscoveryRun) -> TurnRecord:
    finishes = [r for r in run.turns if r.ok and isinstance(r.response, Finish)]
    if len(finishes) != 1:
        raise NotCompilable(run.path, None, "exactly one ok finish turn", str(len(finishes)))
    return finishes[0]


def _check_prefix(extra: DiscoveryRun, steps: list[Step]) -> None:
    acts = kept_acts(extra)
    if len(acts) > len(steps):
        raise Incompatible(
            extra.path, acts[len(steps)].index, f"at most {len(steps)} steps", f"{len(acts)} ok act turns"
        )
    for record, step in zip(acts, steps):
        turn = record.response
        assert isinstance(turn, Act) and record.postcondition is not None
        recorded = (turn.action, bare(record.target), without_evidence(record.postcondition))
        expected = (step.action, bare(step.target), without_evidence(step.postcondition))
        if recorded != expected:
            raise Incompatible(
                extra.path,
                record.index,
                f"the same action, target and postcondition as step {step.step_id!r}",
                f"{turn.action.kind} on {_describe(record.target)} expecting {record.postcondition.kind}",
            )


def _settled_pages(run: DiscoveryRun) -> list[str]:
    """The observation before each turn that follows an ok act is the page that
    act settled on; the finish turn's is the final page."""
    return [
        run.turns[i + 1].ax_snapshot
        for i, record in enumerate(run.turns[:-1])
        if record.ok and isinstance(record.response, Act)
    ]


def _describe(target: TargetDescriptor | None) -> str:
    if target is None:
        return "no target"
    return f"{target.role} {target.accessible_name or target.label!r}"
