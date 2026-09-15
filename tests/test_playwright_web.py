"""End-to-end: the hand-written artifact's descriptors, the real app, the real
accessibility tree, and the resolver all agree. Headless so `pytest` opens no
window; the CLI path stays headed."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from werkzeug.serving import make_server

from app.server import create_app
from cua.schema import Capability, TargetDescriptor
from cua.surface import (
    Click,
    Navigate,
    PlaywrightWebSurface,
    ReadText,
    StaleNode,
    TypeText,
    resolve,
)

ARTIFACT = Path(__file__).resolve().parents[1] / "artifacts" / "lookup_member_status.json"


@pytest.fixture(scope="module")
def base_url() -> Iterator[str]:
    server = make_server("127.0.0.1", 0, create_app("a"), threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture(scope="module")
def targets() -> dict[str, TargetDescriptor]:
    capability = Capability.model_validate(json.loads(ARTIFACT.read_text()))
    return {s.step_id: s.target for s in capability.steps if s.target is not None}


def test_artifact_targets_resolve_and_act(base_url: str, targets: dict[str, TargetDescriptor]) -> None:
    with PlaywrightWebSurface(base_url, headless=True) as surface:
        surface.act(Navigate("/members/search"), None)
        first = surface.observe()
        assert first.location == "/members/search"

        box = resolve(first, targets["enter_member_id"])
        assert box.tier == "name"
        button = resolve(first, targets["submit_search"])
        assert button.tier == "container"

        surface.act(TypeText("10001"), box.node)
        typed = surface.observe()
        assert typed.by_id(box.node.node_id) is None  # ids are snapshot-scoped
        assert resolve(typed, targets["enter_member_id"]).node.value == "10001"

        with pytest.raises(StaleNode):
            surface.act(Click(), button.node)

        surface.act(Click(), resolve(typed, targets["submit_search"]).node)
        record = surface.observe()
        assert record.location == "/member/10001"
        assert {tuple(n.frame_path) for n in record.nodes} == {(), ("Member Record",)}

        status = resolve(record, targets["read_plan_status"])
        renewal = resolve(record, targets["read_renewal_date"])
        assert status.tier == "label" and renewal.tier == "label"
        assert surface.act(ReadText(), status.node) == "Active"
        assert surface.act(ReadText(), renewal.node) == "2027-01-15"


def test_graph_carries_nothing_web_specific(base_url: str) -> None:
    with PlaywrightWebSurface(base_url, headless=True) as surface:
        surface.act(Navigate("/member/10001"), None)
        dumped = surface.observe().model_dump_json()
    assert "http" not in dumped
    assert "backend" not in dumped.lower()
    assert "ctl_" not in dumped.replace("Session ctl_", "")  # regenerated ids only appear as page text
