"""What a human changed on the surface while holding control, as the difference
between the observation before the handoff and the one after. Node ids are
snapshot-scoped and useless across the two, so nodes are compared by what the
resolver would see: frame, role, name, label, text, value."""

from __future__ import annotations

from collections import Counter

from cua.schema import StrictModel
from cua.surface import ElementNode, Observation


class NodeSummary(StrictModel):
    frame_path: list[str]
    role: str
    name: str
    label: str | None
    text: str
    value: str | None


class AxDiff(StrictModel):
    location_before: str
    location_after: str
    added: list[NodeSummary]
    removed: list[NodeSummary]


def diff(before: Observation, after: Observation) -> AxDiff:
    old = Counter(_key(n) for n in before.nodes)
    new = Counter(_key(n) for n in after.nodes)
    return AxDiff(
        location_before=before.location,
        location_after=after.location,
        added=[_summary(k) for k in sorted((new - old).elements(), key=_sortable)],
        removed=[_summary(k) for k in sorted((old - new).elements(), key=_sortable)],
    )


_Key = tuple[tuple[str, ...], str, str, str | None, str, str | None]


def _key(node: ElementNode) -> _Key:
    return (tuple(node.frame_path), node.role, node.name, node.label, node.text, node.value)


def _sortable(key: _Key) -> tuple[tuple[str, ...], str, str, str, str, str]:
    frame_path, role, name, label, text, value = key
    return (frame_path, role, name, label or "", text, value or "")


def _summary(key: _Key) -> NodeSummary:
    frame_path, role, name, label, text, value = key
    return NodeSummary(
        frame_path=list(frame_path), role=role, name=name, label=label, text=text, value=value
    )
