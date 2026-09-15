"""One replay's evidence directory, `evidence/<run_id>/`: the redacted event log,
the redacted result, and for every completed step the observation that satisfied
its postcondition with a masked screenshot of it. A failure adds the state the run
stopped in. Every text file passes the run's redactor at the sink (invariant 6);
PNGs arrive masked by the surface under the same rule (`escalation.mask`).

A step re-run after a handoff overwrites its own files: the result's trace list
already shows the detour, and `interventions/<n>/` holds the handoff's before/after.
"""

from __future__ import annotations

from pathlib import Path
from typing import TextIO

from cua.escalation import Snapshot, minimal_masks, write_snapshot
from cua.policy import Redactor
from cua.schema import ReplayResult
from cua.surface import Observation, SurfaceNotReady

from .session import Session


def snapshot(session: Session) -> Snapshot | None:
    """The surface may be exactly as unobservable as a failure says it is (a hung
    navigation); then there is no state to record, and that is recorded."""
    try:
        observation = session.observe()
        png = session.capture(minimal_masks(observation, session.log.redactor))
    except SurfaceNotReady:
        return None
    return Snapshot(observation, png)


class RunEvidence:
    def __init__(self, directory: Path, redactor: Redactor) -> None:
        self.directory = directory
        self._redactor = redactor
        (directory / "ax").mkdir(parents=True)
        self._events: TextIO = (directory / "events.jsonl").open("w")

    @property
    def events(self) -> TextIO:
        return self._events

    def step(self, step_id: str, observation: Observation, session: Session) -> None:
        """The observation is the one that satisfied the postcondition; the screenshot
        is taken now, of the page it describes."""
        png = session.capture(minimal_masks(observation, self._redactor))
        write_snapshot(self.directory / "ax" / f"{step_id}.json", observation, self._redactor)
        (self.directory / "ax" / f"{step_id}.png").write_bytes(png)

    def failure(self, snap: Snapshot | None) -> None:
        if snap is None:
            (self.directory / "failure.unobservable").write_text("surface not ready\n")
            return
        write_snapshot(self.directory / "failure.json", snap.observation, self._redactor)
        (self.directory / "failure.png").write_bytes(snap.png)

    def result(self, result: ReplayResult) -> None:
        # Outputs are the caller's answer on stdout; on disk they are evidence.
        text = self._redactor.redact(result.model_dump_json(indent=2))
        (self.directory / "result.json").write_text(text + "\n")

    def close(self) -> None:
        self._events.close()
