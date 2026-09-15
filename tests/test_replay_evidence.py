"""A replay leaves one directory per run: the redacted event log and result, the
postcondition-satisfying observation and a masked screenshot for every completed
step, and the state the run stopped in when it failed. No parameter value reaches
any text file."""

from __future__ import annotations

import json
from pathlib import Path

from cua.replay import replay
from cua.schema import FailureKind, ReplayResult, ReplayStatus

from .fixtures import ScriptedSurface, capability, forbidden_page, profile

PARAMS = {"member_id": "10001"}


def text_files(directory: Path) -> list[Path]:
    return sorted(p for p in directory.rglob("*") if p.suffix in {".json", ".jsonl"})


def test_clean_run_records_every_step(tmp_path: Path) -> None:
    surface = ScriptedSurface()
    result = replay(capability(), PARAMS, surface, profile(), evidence_dir=tmp_path)

    directory = tmp_path / result.run_id
    assert result.status is ReplayStatus.success
    assert sorted(p.name for p in directory.iterdir()) == ["ax", "events.jsonl", "result.json"]
    step_ids = [s.step_id for s in capability().steps]
    assert sorted(p.name for p in (directory / "ax").iterdir()) == sorted(
        f"{s}{ext}" for s in step_ids for ext in (".json", ".png")
    )
    # One masked capture per completed step, of the page the postcondition held on.
    assert len(surface.captured) == len(step_ids)
    assert surface.captured[1] == ["box"]  # the typed id is masked

    events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
    assert [e["event"] for e in events if e["event"] == "step_completed"] == ["step_completed"] * len(step_ids)
    assert events[-1]["event"] == "run_finished" and events[-1]["status"] == "success"

    on_disk = ReplayResult.model_validate_json((directory / "result.json").read_text())
    assert on_disk.run_id == result.run_id and on_disk.outputs == result.outputs
    for path in text_files(directory):
        assert "10001" not in path.read_text(), path.name


def test_failed_run_records_the_state_it_stopped_in(tmp_path: Path) -> None:
    surface = ScriptedSurface(final=forbidden_page())
    result = replay(capability(), PARAMS, surface, profile(), evidence_dir=tmp_path)

    directory = tmp_path / result.run_id
    assert result.status is ReplayStatus.failed and result.failure is not None
    assert result.failure.kind is FailureKind.permission_denied
    assert (directory / "failure.json").exists() and (directory / "failure.png").exists()
    assert json.loads((directory / "failure.json").read_text())["location"] == forbidden_page().location
    assert json.loads((directory / "result.json").read_text())["failure"]["kind"] == "permission_denied"
    for path in text_files(directory):
        assert "10001" not in path.read_text(), path.name


def test_no_evidence_dir_writes_nothing(tmp_path: Path) -> None:
    surface = ScriptedSurface()
    replay(capability(), PARAMS, surface, profile())
    assert list(tmp_path.iterdir()) == [] and surface.captured == []
