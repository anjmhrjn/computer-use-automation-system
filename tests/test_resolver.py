from __future__ import annotations

import pytest

from cua.schema import ContainerHint, NameMatch
from cua.surface import Ambiguous, Observation, Unresolvable, resolve

from .fixtures import descriptor
from .fixtures import record_frame as build_record_frame
from .fixtures import search_page as build_search_page


@pytest.fixture
def search_page() -> Observation:
    return build_search_page()


@pytest.fixture
def record_frame() -> Observation:
    return build_record_frame()


def test_name_alone_resolves_and_is_recorded(search_page: Observation) -> None:
    r = resolve(search_page, descriptor("textbox", "Member ID"))
    assert r.node.node_id == "box"
    assert r.tier == "name"


def test_name_contains(search_page: Observation) -> None:
    r = resolve(search_page, descriptor("textbox", "Member", match=NameMatch.contains))
    assert r.node.node_id == "box"


def test_name_normalized(search_page: Observation) -> None:
    r = resolve(search_page, descriptor("textbox", "  member id:", match=NameMatch.normalized))
    assert r.node.node_id == "box"


def test_ambiguous_name_falls_through_to_container(search_page: Observation) -> None:
    d = descriptor("button", "Search", container=ContainerHint(role="form", accessible_name="Member search"))
    r = resolve(search_page, d)
    assert r.node.node_id == "btn"
    assert r.tier == "container"
    assert [t.tier for t in r.trace] == ["frame+role", "name", "container"]


def test_ambiguous_after_all_signals_raises(search_page: Observation) -> None:
    with pytest.raises(Ambiguous) as info:
        resolve(search_page, descriptor("button", "Search"))
    assert len(info.value.candidates) == 2
    assert info.value.trace[-1].after == 2


def test_label_tier(record_frame: Observation) -> None:
    d = descriptor("definition", label="Plan Status", frame=["Member Record"])
    r = resolve(record_frame, d)
    assert r.node.text == "Active"
    assert r.tier == "label"


def test_nearby_text_tier(search_page: Observation) -> None:
    d = descriptor("textbox", nearby=["Find a member"])
    r = resolve(search_page, d)
    assert r.node.node_id == "box"
    assert r.tier == "nearby_text"


def test_ordinal_picks_explicit_index_only_as_last_resort(search_page: Observation) -> None:
    r = resolve(search_page, descriptor("button", "Search", ordinal=1))
    assert r.node.node_id == "btn"
    assert r.tier == "ordinal"
    with pytest.raises(Unresolvable):
        resolve(search_page, descriptor("button", "Search", ordinal=5))


def test_stale_signal_is_skipped_not_fatal(search_page: Observation) -> None:
    d = descriptor("textbox", "Member ID", label="Renamed Since Discovery")
    r = resolve(search_page, d)
    assert r.node.node_id == "box"
    assert r.tier == "name"

    d = descriptor("button", "Search", label="Nope", container=ContainerHint(role="form", accessible_name="Member search"))
    r = resolve(search_page, d)
    assert r.tier == "container"
    skipped = [t for t in r.trace if t.skipped]
    assert not skipped  # label never reached: container resolved first

    d = descriptor("button", "Search", container=ContainerHint(role="dialog", accessible_name=None), ordinal=0)
    r = resolve(search_page, d)
    assert r.node.node_id == "site_btn"
    assert any(t.tier == "container" and t.skipped for t in r.trace)


def test_frame_path_is_a_hard_scope(record_frame: Observation) -> None:
    with pytest.raises(Unresolvable):
        resolve(record_frame, descriptor("definition", label="Plan Status"))
    with pytest.raises(Unresolvable):
        resolve(record_frame, descriptor("heading", "Member", match=NameMatch.contains, frame=["Record"]))


def test_container_never_crosses_frames(record_frame: Observation) -> None:
    # Two top-level headings contain "Member"; the only "Coverage" group is inside
    # the iframe, so the container signal must match nothing rather than reach in.
    d = descriptor("heading", "Member", match=NameMatch.contains,
                   container=ContainerHint(role="group", accessible_name="Coverage"))
    with pytest.raises(Ambiguous) as info:
        resolve(record_frame, d)
    assert any(t.tier == "container" and t.skipped for t in info.value.trace)


def test_unknown_role_is_unresolvable(search_page: Observation) -> None:
    with pytest.raises(Unresolvable) as info:
        resolve(search_page, descriptor("combobox", "Anything"))
    assert info.value.candidates == []


def test_strict_does_not_skip_a_stale_signal(search_page: Observation) -> None:
    d = descriptor("button", "Search", label="Nope", container=ContainerHint(role="form", accessible_name="Member search"))
    assert resolve(search_page, d).node.node_id == "btn"  # tolerant: label skipped
    with pytest.raises(Unresolvable) as info:
        resolve(search_page, d, strict=True)
    assert info.value.trace[-1].tier == "label"


def test_strict_checks_every_signal_even_with_one_candidate(record_frame: Observation) -> None:
    only_group = descriptor("group", "Billing", frame=["Member Record"])
    assert resolve(record_frame, only_group).node.node_id == "grp"
    with pytest.raises(Unresolvable):
        resolve(record_frame, only_group, strict=True)
