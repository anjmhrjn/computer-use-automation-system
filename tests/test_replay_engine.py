"""End-to-end: the hand-written artifact replayed against the real app through the
real adapter, both declared outcomes and one hard failure. Headless."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from werkzeug.serving import make_server

from app.server import create_app
from cua.replay import PostconditionTimeout, replay
from cua.schema import Capability
from cua.surface import PlaywrightWebSurface

ARTIFACT = Path(__file__).resolve().parents[1] / "artifacts" / "lookup_member_status.json"


@pytest.fixture(scope="module")
def base_url() -> Iterator[str]:
    server = make_server("127.0.0.1", 0, create_app("a"), threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture(scope="module")
def raw() -> dict[str, object]:
    return json.loads(ARTIFACT.read_text())


@pytest.fixture(scope="module")
def capability(raw: dict[str, object]) -> Capability:
    return Capability.model_validate(raw)


def test_found_member(base_url: str, capability: Capability) -> None:
    with PlaywrightWebSurface(base_url, headless=True) as surface:
        result = replay(capability, {"member_id": "10001"}, surface)
    assert result.outcome == "found"
    assert result.outputs == {"plan_status": "Active", "renewal_date": "2027-01-15"}
    assert [t.step_id for t in result.steps] == [s.step_id for s in capability.steps]
    assert [t.resolved_tier for t in result.steps] == [None, "name", "container", "label", "label"]
    assert all(t.elapsed_ms >= 0 for t in result.steps)


def test_no_such_member_is_an_outcome_not_a_failure(base_url: str, capability: Capability) -> None:
    with PlaywrightWebSurface(base_url, headless=True) as surface:
        result = replay(capability, {"member_id": "99999"}, surface)
    assert result.outcome == "no_such_member"
    assert result.outputs == {}
    assert [t.step_id for t in result.steps] == ["open_search", "enter_member_id", "submit_search"]


def test_postcondition_timeout_names_the_step(base_url: str, raw: dict[str, object]) -> None:
    broken = json.loads(json.dumps(raw))
    step = broken["steps"][0]
    step["postcondition"]["target"]["accessible_name"] = "Nope"
    step["postcondition_timeout_ms"] = 300
    capability = Capability.model_validate(broken)
    with PlaywrightWebSurface(base_url, headless=True) as surface:
        with pytest.raises(PostconditionTimeout) as info:
            replay(capability, {"member_id": "10001"}, surface)
    assert info.value.step_id == "open_search"
    assert "Nope" in info.value.expected
    assert "/members/search" in info.value.observed
