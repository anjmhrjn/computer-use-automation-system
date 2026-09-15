from __future__ import annotations

from cua.replay import Session, describe, holds
from cua.schema import (
    ContainerHint,
    ElementAbsent,
    ElementPresent,
    LiteralValue,
    LocationMatches,
    NameMatch,
    ParameterValue,
    TextPresent,
    ValueEquals,
)
from cua.surface import ElementNode, Observation, Surface

from .fixtures import descriptor, record_frame, search_page, with_value


class NoSurface:
    """A predicate must never observe or act; it only reads the observation it is given."""

    def observe(self) -> Observation:
        raise AssertionError("predicate observed")

    def act(self, action: object, node: ElementNode | None) -> str | None:
        raise AssertionError("predicate acted")


def session(**params: str) -> Session:
    surface: Surface = NoSurface()
    return Session(surface, params)


def test_element_present() -> None:
    page = search_page()
    assert holds(ElementPresent(kind="element_present", target=descriptor("textbox", "Member ID")), page, session())
    assert not holds(ElementPresent(kind="element_present", target=descriptor("textbox", "Nope")), page, session())


def test_element_present_is_false_when_ambiguous() -> None:
    page = search_page()
    two_buttons = ElementPresent(kind="element_present", target=descriptor("button", "Search"))
    assert not holds(two_buttons, page, session())


def test_element_absent() -> None:
    page = search_page()
    assert holds(ElementAbsent(kind="element_absent", target=descriptor("textbox", "Nope")), page, session())
    assert not holds(ElementAbsent(kind="element_absent", target=descriptor("textbox", "Member ID")), page, session())
    # Two matches is not absent either.
    assert not holds(ElementAbsent(kind="element_absent", target=descriptor("button", "Search")), page, session())


def test_text_present_is_frame_scoped_and_normalized() -> None:
    page = record_frame()
    in_frame = TextPresent(kind="text_present", text="  active ", scope=None, frame_path=["Member Record"])
    top_level = TextPresent(kind="text_present", text="Active", scope=None, frame_path=[])
    assert holds(in_frame, page, session())
    assert not holds(top_level, page, session())


def test_text_present_honours_scope() -> None:
    page = record_frame()
    coverage = ContainerHint(role="group", accessible_name="Coverage")
    elsewhere = ContainerHint(role="group", accessible_name="Billing")
    assert holds(TextPresent(kind="text_present", text="Active", scope=coverage, frame_path=["Member Record"]), page, session())
    assert not holds(TextPresent(kind="text_present", text="Active", scope=elsewhere, frame_path=["Member Record"]), page, session())


def test_location_matches_is_a_glob() -> None:
    page = record_frame()
    assert holds(LocationMatches(kind="location_matches", pattern="/member/*"), page, session())
    assert not holds(LocationMatches(kind="location_matches", pattern="/members/*"), page, session())


def test_value_equals_with_parameter_and_literal() -> None:
    page = with_value(search_page(), "box", "10001")
    target = descriptor("textbox", "Member ID")
    by_param = ValueEquals(kind="value_equals", target=target, expected=ParameterValue(kind="parameter", name="member_id"))
    by_literal = ValueEquals(kind="value_equals", target=target, expected=LiteralValue(kind="literal", text="10001"))
    assert holds(by_param, page, session(member_id="10001"))
    assert not holds(by_param, page, session(member_id="99999"))
    assert holds(by_literal, page, session())


def test_describe_names_parameters_without_values() -> None:
    target = descriptor("textbox", "Member ID", match=NameMatch.exact)
    text = describe(ValueEquals(kind="value_equals", target=target, expected=ParameterValue(kind="parameter", name="member_id")))
    assert "member_id" in text
    assert "10001" not in text
