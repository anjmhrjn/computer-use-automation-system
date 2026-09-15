"""Hand-built observations shared by the resolver, predicate, and engine tests.
Plain functions, not fixtures, so a test can build one and then mutate a copy."""

from __future__ import annotations

import json
from pathlib import Path

from cua.schema import AppProfile, ContainerHint, NameMatch, TargetDescriptor
from cua.surface import ElementNode, Observation

PROFILE = Path(__file__).resolve().parents[1] / "artifacts" / "apps" / "memberserve.json"


def profile() -> AppProfile:
    return AppProfile.model_validate(json.loads(PROFILE.read_text()))


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


def with_value(observation: Observation, node_id: str, value: str) -> Observation:
    nodes = [
        n.model_copy(update={"value": value}) if n.node_id == node_id else n
        for n in observation.nodes
    ]
    return observation.model_copy(update={"nodes": nodes})


def interstitial_page(heading: str, alert: str, location: str, *, dismiss: str | None = None) -> Observation:
    """An interstitial as MemberServe serves one: heading, alert, and optionally a
    single dismiss button, at the location the automation asked for."""
    nodes = [
        node("root", "RootWebArea", "MemberServe", order=0),
        node("banner", "heading", "Member Services Console", parent="root", order=1),
        node("h", "heading", heading, parent="root", order=2),
        node("alert", "alert", alert, parent="root", order=3),
    ]
    if dismiss is not None:
        nodes.append(node("btn", "button", dismiss, parent="root", order=4))
    return Observation(location=location, nodes=nodes)


def session_expired_page() -> Observation:
    return interstitial_page("Session expired", "Your session has expired.", "/members/search")


def maintenance_page() -> Observation:
    return interstitial_page(
        "Scheduled maintenance", "MemberServe will be unavailable.", "/members/search", dismiss="Continue"
    )


def forbidden_page() -> Observation:
    return interstitial_page(
        "Access denied", "You do not have permission to view this record.", "/member/20001"
    )


def not_found_page() -> Observation:
    return Observation(
        location="/member/99999",
        nodes=[
            node("root", "RootWebArea", "MemberServe", order=0),
            node("h", "heading", "Member 99999", parent="root", order=1),
            node("status", "status", "No member found for id 99999.", parent="root", order=2),
        ],
    )
