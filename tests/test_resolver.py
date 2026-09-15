from __future__ import annotations

import pytest

from cua.schema import ContainerHint, NameMatch, TargetDescriptor
from cua.surface import Ambiguous, ElementNode, Observation, Unresolvable, resolve


def node(
    node_id: str,
    role: str,
    name: str = "",
    *,
    parent: str | None = None,
    frame: list[str] | None = None,
    label: str | None = None,
    text: str | None = None,
    order: int = 0,
) -> ElementNode:
    return ElementNode(
        node_id=node_id,
        role=role,
        name=name,
        value=None,
        text=text if text is not None else name,
        label=label,
        frame_path=frame or [],
        parent_id=parent,
        order=order,
    )


def descriptor(
    role: str,
    name: str | None = None,
    *,
    match: NameMatch = NameMatch.exact,
    label: str | None = None,
    nearby: list[str] | None = None,
    container: ContainerHint | None = None,
    ordinal: int | None = None,
    frame: list[str] | None = None,
) -> TargetDescriptor:
    return TargetDescriptor(
        role=role,
        accessible_name=name,
        name_match=match,
        label=label,
        nearby_text=nearby or [],
        container=container,
        ordinal=ordinal,
        frame_path=frame or [],
        evidence=None,
    )


@pytest.fixture
def search_page() -> Observation:
    """Two Search buttons and two textboxes, as MemberServe serves them."""
    return Observation(
        location="/members/search",
        nodes=[
            node("root", "RootWebArea", "MemberServe", order=0),
            node("site", "form", "Site search", parent="root", order=1),
            node("site_box", "textbox", "Site search terms", parent="site", order=2),
            node("site_btn", "button", "Search", parent="site", order=3),
            # Sidebar chrome sits between the site search and the body in document order.
            node("lead", "heading", "Find a member", parent="root", order=20),
            node("form", "form", "Member search", parent="root", order=21),
            node("lbl", "LabelText", "Member ID", parent="form", order=22),
            node("box", "textbox", "Member ID", parent="form", label="Member ID", order=23),
            node("btn", "button", "Search", parent="form", order=24),
        ],
    )


@pytest.fixture
def record_frame() -> Observation:
    frame = ["Member Record"]
    return Observation(
        location="/member/10001",
        nodes=[
            node("root", "RootWebArea", "MemberServe", order=0),
            node("banner", "heading", "Member Services Console", parent="root", order=1),
            node("h", "heading", "Member 10001", parent="root", order=2),
            node("iframe", "Iframe", "Member Record", parent="root", order=3),
            node("fr", "RootWebArea", "Member Record", frame=frame, order=0),
            node("fh", "heading", "Member Record — #10001", parent="fr", frame=frame, order=1),
            node("grp", "group", "Coverage", parent="fr", frame=frame, order=2),
            node("t1", "term", "Plan Status", parent="grp", frame=frame, order=3),
            node("d1", "definition", parent="grp", frame=frame, label="Plan Status", text="Active", order=4),
            node("t2", "term", "Renewal Date", parent="grp", frame=frame, order=5),
            node("d2", "definition", parent="grp", frame=frame, label="Renewal Date", text="2027-01-15", order=6),
        ],
    )


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
