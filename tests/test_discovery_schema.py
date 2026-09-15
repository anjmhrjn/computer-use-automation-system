"""Invariant 10 for the two schemas the model is asked to fill, and the rules that
turn its answers into artifact types."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import BaseModel

from cua.discovery import (
    AmbiguousExpectation,
    Contract,
    DerivationFailed,
    ExpectElement,
    ExpectValue,
    NotStrict,
    TurnEnvelope,
    describe,
    strict_schema,
    to_predicate,
)
from cua.schema import NameMatch, ParameterValue, StrictModel
from cua.surface import Observation, resolve

from .fixtures import node, record_frame, search_page


def _objects(schema: Any):
    if isinstance(schema, dict):
        if schema.get("type") == "object" and "properties" in schema:
            yield schema
        for value in schema.values():
            yield from _objects(value)
    elif isinstance(schema, list):
        for item in schema:
            yield from _objects(item)


@pytest.mark.parametrize("model", [Contract, TurnEnvelope])
def test_schemas_are_strict_mode_compatible(model: type[BaseModel]) -> None:
    schema = strict_schema(model)
    text = json.dumps(schema)
    for forbidden in ('"oneOf"', '"const"', '"discriminator"', '"default"', '"title"'):
        assert forbidden not in text
    for obj in _objects(schema):
        assert obj["additionalProperties"] is False
        assert sorted(obj["required"]) == sorted(obj["properties"])
    assert schema["type"] == "object"


def test_transform_refuses_non_strict_types() -> None:
    class Loose(StrictModel):
        name: str = "x"

    with pytest.raises(NotStrict):
        strict_schema(Loose)


def test_derive_adds_container_only_when_needed() -> None:
    page = search_page()
    form_button = describe(page, page.by_id("btn"), "ax/000.json")
    assert form_button.container is not None
    assert form_button.container.accessible_name == "Member search"
    assert form_button.evidence is not None and form_button.evidence.ax_snapshot == "ax/000.json"
    assert resolve(page, form_button, strict=True).node.node_id == "btn"

    site_button = describe(page, page.by_id("site_btn"), "ax/000.json")
    assert site_button.container is not None
    assert site_button.container.accessible_name == "Site search"

    box = describe(page, page.by_id("box"), "ax/000.json")
    assert box.container is None and box.label == "Member ID"


def test_derive_unnamed_definition_by_label() -> None:
    page = record_frame()
    status = describe(page, page.by_id("d1"), "ax/003.json")
    assert status.accessible_name is None and status.label == "Plan Status"
    assert resolve(page, status, strict=True).node.node_id == "d1"


def test_derive_falls_to_nearby_then_ordinal() -> None:
    page = Observation(
        location="/x",
        nodes=[
            node("root", "RootWebArea", "App", order=0),
            # Two sections further apart than the nearby-text window.
            node("h1", "heading", "Billing", parent="root", order=1),
            node("b1", "button", "Edit", parent="root", order=2),
            node("h2", "heading", "Shipping", parent="root", order=30),
            node("b2", "button", "Edit", parent="root", order=31),
            node("b3", "button", "Edit", parent="root", order=32),
        ],
    )
    first = describe(page, page.by_id("b1"), "ax")
    assert first.nearby_text == ["Billing"] and first.ordinal is None
    third = describe(page, page.by_id("b3"), "ax")
    assert third.nearby_text == ["Shipping"] and third.ordinal == 1
    assert resolve(page, third, strict=True).node.node_id == "b3"


def test_derive_raises_when_nothing_pins_the_node() -> None:
    page = Observation(
        location="/x",
        nodes=[
            node("root", "RootWebArea", "App", order=0),
            node("b1", "button", "Edit", parent="root", order=1),
        ],
    )
    # The resolver keys on the picked node's own fields, so a node that is not in
    # the observation it is described against cannot resolve.
    stray = node("zz", "button", "Save", parent="root", order=9)
    with pytest.raises(DerivationFailed):
        describe(page, stray, "ax")


def test_to_predicate_element_ambiguity_is_an_error() -> None:
    page = record_frame()
    expect = ExpectElement(
        kind="element", role="definition", accessible_name="", name_match="exact", label=None,
        frame_path=["Member Record"],
    )
    with pytest.raises(AmbiguousExpectation):
        to_predicate(expect, page, page, "ax")
    unique = expect.model_copy(update={"role": "heading", "accessible_name": "Member Record",
                                       "name_match": NameMatch.contains})
    predicate = to_predicate(unique, page, page, "ax")
    assert predicate is not None and predicate.kind == "element_present"
    assert to_predicate(unique, page, None, "ax") is None


def test_to_predicate_value_uses_the_pre_action_node() -> None:
    page = search_page()
    ref = ParameterValue(kind="parameter", name="member_id")
    predicate = to_predicate(ExpectValue(kind="value", node_id="box", expected=ref), page, None, "ax")
    assert predicate is not None and predicate.kind == "value_equals"
    assert predicate.target.label == "Member ID"


def test_to_predicate_element_by_label() -> None:
    page = record_frame()
    expect = ExpectElement(
        kind="element", role="definition", accessible_name=None, name_match=NameMatch.exact,
        label="Plan Status", frame_path=["Member Record"],
    )
    predicate = to_predicate(expect, page, page, "ax")
    assert predicate is not None and predicate.kind == "element_present"
    assert predicate.target.label == "Plan Status"
    assert resolve(page, predicate.target, strict=True).node.node_id == "d1"

    # Role alone is enough when the page has exactly one such node.
    only_group = ExpectElement(kind="element", role="group", accessible_name=None,
                               name_match=NameMatch.exact, label=None, frame_path=["Member Record"])
    predicate = to_predicate(only_group, page, page, "ax")
    assert predicate is not None and predicate.target.accessible_name == "Coverage"
    with pytest.raises(AmbiguousExpectation):
        to_predicate(expect.model_copy(update={"label": None}), page, page, "ax")
