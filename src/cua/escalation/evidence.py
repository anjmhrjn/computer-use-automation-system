"""Writes one handoff's evidence. Every text file passes the run's redactor at the
sink (invariant 6); the PNGs arrive already masked by the surface, using the same
redactor to choose what to mask (`mask.py`). A side the surface could not observe
(a hung navigation) is recorded as unobservable rather than skipped."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cua.policy import Redactor
from cua.schema import Resolution
from cua.surface import Observation

from .diff import diff


@dataclass(frozen=True)
class Snapshot:
    observation: Observation
    png: bytes


def write_snapshot(path: Path, observation: Observation, redactor: Redactor) -> None:
    path.write_text(redactor.redact(observation.model_dump_json(indent=1)) + "\n")


def write_handoff(
    directory: Path,
    *,
    before: Snapshot | None,
    after: Snapshot | None,
    resolution: Resolution,
    redactor: Redactor,
) -> None:
    directory.mkdir(parents=True)
    for name, snapshot in (("before", before), ("after", after)):
        if snapshot is None:
            (directory / f"{name}.unobservable").write_text("surface not ready\n")
            continue
        write_snapshot(directory / f"{name}.json", snapshot.observation, redactor)
        (directory / f"{name}.png").write_bytes(snapshot.png)
    if before is not None and after is not None:
        changes = diff(before.observation, after.observation)
        (directory / "diff.json").write_text(redactor.redact(changes.model_dump_json(indent=1)) + "\n")
    (directory / "resolution.json").write_text(
        redactor.redact(resolution.model_dump_json(indent=1)) + "\n"
    )
