"""One human handoff: pause, hand the control token to a human, block on the
operator's resolution, take the token back, record what changed, and say where the
engine resumes. Nothing here acts on the surface; the only surface calls are
`observe()` and `capture()`, so the token is safe to hold across them.

The request leaves the process, so the failure text is redacted here, before the
engine's own end-of-run redaction. Parameter names go out; values never do."""

from __future__ import annotations

import secrets
import time
from pathlib import Path

from cua.escalation import EscalationError, Escalator, write_handoff
from cua.schema import (
    Capability,
    Failure,
    FailureKind,
    InterventionRecord,
    InterventionRequest,
    Resolution,
    ResolutionKind,
)
from .evidence import snapshot
from .session import Session


def new_run_id() -> str:
    return time.strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(3)


def handoff(
    capability: Capability,
    session: Session,
    failure: Failure,
    escalator: Escalator,
    run_id: str,
    evidence_dir: Path | None,
    ordinal: int,
) -> tuple[InterventionRecord, int | None]:
    """Returns the record and the index of the step to resume from, `None` for abort."""
    redact = session.log.redactor.redact
    failure = failure.model_copy(
        update={"expected": redact(failure.expected), "observed": redact(failure.observed)}
    )
    step_ids = [s.step_id for s in capability.steps]
    failed_at = step_ids.index(failure.step_id)
    before = snapshot(session)
    request = InterventionRequest(
        request_id=f"{run_id}-{ordinal}",
        run_id=run_id,
        capability_id=capability.capability_id,
        step_id=failure.step_id,
        failure=failure,
        location=None if before is None else redact(before.observation.location),
        resumable_steps=step_ids[: failed_at + 1],
        inputs=[p.name for p in capability.inputs],
        requested_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    )

    session.token.hand_to_human()
    session.log.emit(
        "intervention_requested",
        step_id=failure.step_id,
        request_id=request.request_id,
        failure_kind=failure.kind.value,
    )
    try:
        resolution = escalator.request(request)
    finally:
        session.token.return_to_automation()
    after = snapshot(session)
    session.log.emit(
        "intervention_resolved",
        step_id=failure.step_id,
        request_id=request.request_id,
        resolution=resolution.kind.value,
        resume_from=resolution.resume_from,
    )

    directory = None
    if evidence_dir is not None:
        directory = evidence_dir / run_id / "interventions" / str(ordinal)
        write_handoff(
            directory,
            before=before,
            after=after,
            resolution=resolution,
            redactor=session.log.redactor,
        )

    record = InterventionRecord(
        request_id=request.request_id,
        step_id=failure.step_id,
        failure_kind=failure.kind,
        resolution=resolution,
        evidence_dir=None if directory is None else str(directory),
    )
    return record, _resume_index(resolution, failure, failed_at, request.resumable_steps, session)


def _resume_index(
    resolution: Resolution,
    failure: Failure,
    failed_at: int,
    resumable: list[str],
    session: Session,
) -> int | None:
    if resolution.kind is ResolutionKind.abort:
        return None
    if resolution.kind is ResolutionKind.approve:
        if failure.kind is not FailureKind.approval_required:
            raise EscalationError(
                f"'approve' resolves only approval_required, not {failure.kind.value}"
            )
        session.approve_once(failure.step_id)
        return failed_at
    assert resolution.resume_from is not None
    if resolution.resume_from not in resumable:
        raise EscalationError(f"resume_from {resolution.resume_from!r} is not one of {resumable}")
    return resumable.index(resolution.resume_from)

