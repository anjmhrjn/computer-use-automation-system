from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from cua.schema import Capability, capability_json_schema

ARTIFACT = Path(__file__).resolve().parents[1] / "artifacts" / "lookup_member_status.json"


@pytest.fixture(scope="module")
def raw() -> dict:
    return json.loads(ARTIFACT.read_text())


@pytest.fixture(scope="module")
def capability(raw: dict) -> Capability:
    return Capability.model_validate(raw)


def test_roundtrips_without_loss(raw: dict, capability: Capability) -> None:
    reparsed = Capability.model_validate_json(capability.model_dump_json())
    assert reparsed == capability
    assert json.loads(capability.model_dump_json()) == raw


def test_every_step_has_a_postcondition(capability: Capability) -> None:
    assert capability.steps
    for step in capability.steps:
        assert step.postcondition is not None, step.step_id


def test_missing_required_field_is_rejected(raw: dict) -> None:
    broken = json.loads(json.dumps(raw))
    del broken["checkpoint"]
    with pytest.raises(ValidationError):
        Capability.model_validate(broken)


def test_optional_field_must_still_be_present(raw: dict) -> None:
    """Strict-mode compatibility: nullable, but never defaulted away."""
    broken = json.loads(json.dumps(raw))
    del broken["steps"][0]["target"]
    with pytest.raises(ValidationError):
        Capability.model_validate(broken)


def test_extra_key_is_rejected(raw: dict) -> None:
    broken = json.loads(json.dumps(raw))
    broken["steps"][0]["selector"] = "#member-id"
    with pytest.raises(ValidationError):
        Capability.model_validate(broken)


def test_step_referencing_undeclared_input_is_rejected(raw: dict) -> None:
    broken = json.loads(json.dumps(raw))
    broken["steps"][1]["action"]["value"]["name"] = "not_an_input"
    with pytest.raises(ValidationError, match="undeclared input"):
        Capability.model_validate(broken)


def test_outcome_binding_undeclared_output_is_rejected(raw: dict) -> None:
    broken = json.loads(json.dumps(raw))
    broken["outcomes"][1]["binds"].append("not_an_output")
    with pytest.raises(ValidationError, match="undeclared output"):
        Capability.model_validate(broken)


def test_exactly_one_success_outcome(raw: dict) -> None:
    broken = json.loads(json.dumps(raw))
    broken["outcomes"][0]["kind"] = "success"
    with pytest.raises(ValidationError, match="exactly one success outcome"):
        Capability.model_validate(broken)


BANNED_SUBSTRINGS = ("selector", "xpath", "css", "coordinate", "pixel")


def test_no_locator_escape_hatch_in_the_schema() -> None:
    """Invariant 1: there is nowhere in the artifact to put a selector."""
    schema = capability_json_schema()
    names = set()
    for definition in schema["$defs"].values():
        names.update(definition.get("properties", {}))
    names.update(schema.get("properties", {}))
    for name in names:
        lowered = name.lower()
        assert not any(banned in lowered for banned in BANNED_SUBSTRINGS), name
    assert {"x", "y"}.isdisjoint(names)


def test_artifact_declares_versions(capability: Capability) -> None:
    assert capability.schema_version == "1.0"
    assert capability.version
    assert capability.target.app_version
