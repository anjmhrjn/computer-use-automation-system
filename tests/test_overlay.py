"""The tenant overlay: renames reach every descriptor signal and nothing else, the
report of what fired is exact, and — live — it is the only thing standing between
the base artifact and variant-b."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import ValidationError
from werkzeug.serving import make_server

from app.server import create_app
from cua.replay import OverlayError, apply_overlay, replay
from cua.schema import (
    Capability,
    ContainerHint,
    ElementPresent,
    ReplayStatus,
    TenantOverlay,
    TextPresent,
    ValueEquals,
)
from cua.surface import PlaywrightWebSurface

from .fixtures import capability, profile, raw_capability

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "artifacts" / "tenants" / "b.json"


def overlay() -> TenantOverlay:
    return TenantOverlay.model_validate_json(OVERLAY.read_text())


def strings(model: Capability) -> list[str]:
    """Every string value anywhere in a model, for asserting what did not change."""
    found: list[str] = []

    def walk(value: object) -> None:
        if isinstance(value, str):
            found.append(value)
        elif isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    walk(json.loads(model.model_dump_json()))
    return found


def test_every_signal_field_is_renamed() -> None:
    rewritten, _ = apply_overlay(capability(), overlay())
    steps = {s.step_id: s for s in rewritten.steps}

    typed = steps["enter_member_id"].target
    assert typed is not None
    assert typed.accessible_name == "Member No." and typed.label == "Member No."
    assert typed.container is not None and typed.container.accessible_name == "Member lookup"

    submit = steps["submit_search"].target
    assert submit is not None
    assert submit.accessible_name == "Find" and submit.nearby_text == ["Member No."]

    read = steps["read_plan_status"].target
    assert read is not None
    assert read.label == "Status" and read.frame_path == ["Record"]
    assert read.nearby_text == ["Plan"]
    assert read.container is not None and read.container.accessible_name == "Plan"

    post = steps["enter_member_id"].postcondition
    assert isinstance(post, ValueEquals) and post.target.accessible_name == "Member No."
    check = rewritten.checkpoint
    assert isinstance(check, ElementPresent) and check.target.frame_path == ["Record"]
    # The same base string is a frame title and a heading; each goes its own way.
    success = next(o for o in rewritten.outcomes if o.name == "found")
    assert isinstance(success.detector, ElementPresent)
    assert success.detector.target.accessible_name == "Member File"
    assert success.detector.target.frame_path == ["Record"]


def test_text_present_predicate_is_renamed() -> None:
    raw = raw_capability()
    raw["checkpoint"] = {
        "kind": "text_present",
        "text": "Plan Status",
        "scope": {"role": "group", "accessible_name": "Coverage"},
        "frame_path": ["Member Record"],
    }
    rewritten, _ = apply_overlay(Capability.model_validate(raw), overlay())
    check = rewritten.checkpoint
    assert isinstance(check, TextPresent)
    assert check.text == "Status"
    assert check.scope == ContainerHint(role="group", accessible_name="Plan")
    assert check.frame_path == ["Record"]


def test_only_signal_strings_change() -> None:
    raw = raw_capability()
    # Non-signal fields that happen to spell a rename key must survive untouched.
    raw["steps"][1]["intent"] = "Member ID"
    raw["steps"][0]["action"]["location"] = "Search"
    raw["description"] = "Coverage"
    before = Capability.model_validate(raw)
    rewritten, _ = apply_overlay(before, overlay())

    assert rewritten.steps[1].intent == "Member ID"
    assert rewritten.steps[0].action.model_dump()["location"] == "Search"
    assert rewritten.description == "Coverage"
    assert [s.step_id for s in rewritten.steps] == [s.step_id for s in before.steps]
    assert rewritten.inputs == before.inputs and rewritten.outputs == before.outputs
    assert rewritten.capability_id == before.capability_id
    assert rewritten.provenance == before.provenance
    # No base string that is a rename key survives anywhere in a signal position.
    keys = set(overlay().renames) | set(overlay().frames)
    assert not (keys - {"Member ID", "Search", "Coverage"}) & set(strings(rewritten))


def test_partial_matches_are_not_rewritten() -> None:
    raw = raw_capability()
    raw["steps"][2]["target"]["accessible_name"] = "Search members"
    rewritten, applied = apply_overlay(Capability.model_validate(raw), overlay())
    target = rewritten.steps[2].target
    assert target is not None and target.accessible_name == "Search members"
    assert "Search" not in applied.applied


def test_reports_applied_and_unused_exactly() -> None:
    raw = OVERLAY.read_text()
    extended = json.loads(raw)
    extended["renames"]["Look up"] = "Find a member"
    extended["frames"]["Sidebar"] = "Nav"
    _, applied = apply_overlay(capability(), TenantOverlay.model_validate(extended))
    assert applied.tenant_id == "b"
    assert applied.applied == (
        "Coverage",
        "Find a member",
        "Member ID",
        "Member Record",
        "Member search",
        "Plan Status",
        "Renewal Date",
        "Search",
        "frame:Member Record",
    )
    assert applied.unused == ("Look up", "frame:Sidebar")


def test_rewritten_capability_roundtrips_and_is_deterministic() -> None:
    once, _ = apply_overlay(capability(), overlay())
    twice, _ = apply_overlay(capability(), overlay())
    assert once.model_dump_json() == twice.model_dump_json()
    assert Capability.model_validate_json(once.model_dump_json()) == once


def test_app_id_mismatch_is_an_error() -> None:
    wrong = overlay().model_copy(update={"app_id": "other"})
    with pytest.raises(OverlayError) as excinfo:
        apply_overlay(capability(), wrong)
    assert "memberserve" in excinfo.value.expected and "other" in excinfo.value.observed


@pytest.mark.parametrize(
    ("renames", "frames"),
    [
        ({}, {}),
        ({"Search": "Search"}, {}),
        ({"Member ID": "Field", "Member No.": "Field"}, {}),
        ({}, {"Member Record": "Member Record"}),
    ],
)
def test_overlay_validation(renames: dict[str, str], frames: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        TenantOverlay(
            schema_version="1.0", tenant_id="x", app_id="memberserve", renames=renames, frames=frames
        )


@pytest.fixture(scope="module")
def variant_b_url() -> Iterator[str]:
    server = make_server("127.0.0.1", 0, create_app("b"), threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_base_artifact_fails_on_variant_b_without_the_overlay(variant_b_url: str) -> None:
    with PlaywrightWebSurface(variant_b_url, headless=True) as surface:
        result = replay(capability(), {"member_id": "10001"}, surface, profile())
    assert result.status is ReplayStatus.failed and result.failure is not None
    assert result.failure.step_id == "open_search"


def test_base_artifact_succeeds_on_variant_b_with_the_overlay(variant_b_url: str) -> None:
    with PlaywrightWebSurface(variant_b_url, headless=True) as surface:
        result = replay(
            capability(), {"member_id": "10001"}, surface, profile(), overlay=overlay()
        )
    assert result.status is ReplayStatus.success, result.failure
    assert result.outcome == "found"
    assert set(result.outputs) == {"plan_status", "renewal_date"}
    assert result.capability_id == capability().capability_id
