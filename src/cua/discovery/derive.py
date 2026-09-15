"""Turns the model's choices into artifact types by rules alone.

`describe()` builds a `TargetDescriptor` for a node the model picked, adding
signals one at a time -- name, then container, then nearby text, then ordinal --
until the existing resolver, in strict mode, lands on exactly that node. The
descriptor is therefore proven replayable at the moment it is recorded, with the
same code that will resolve it later. `to_predicate()` upgrades the model's reduced
`Expect` into a full `Predicate` the same way."""

from __future__ import annotations

from cua.schema import (
    ContainerHint,
    ElementPresent,
    EvidenceRef,
    LocationMatches,
    NameMatch,
    Predicate,
    TargetDescriptor,
    TextPresent,
    ValueEquals,
)
from cua.surface import (
    NEARBY_WINDOW,
    Ambiguous,
    ElementNode,
    Observation,
    Unresolvable,
    name_matches,
    normalize,
    resolve,
)

from .turns import Expect, ExpectElement, ExpectLocation, ExpectText, ExpectValue

CONTAINER_ROLES = ("form", "group", "region", "dialog", "navigation", "main", "table", "article")
LANDMARK_TEXT_ROLES = ("heading", "LabelText", "legend", "term")


class DerivationFailed(Exception):
    def __init__(self, node: ElementNode, reason: str) -> None:
        self.node = node
        super().__init__(f"{node.role} {node.name!r}: {reason}")


class AmbiguousExpectation(Exception):
    def __init__(self, expect: ExpectElement, count: int) -> None:
        self.expect = expect
        super().__init__(
            f"{count} nodes match {expect.role!r} "
            f"name={expect.accessible_name!r} label={expect.label!r}; name something unique"
        )


def describe(observation: Observation, node: ElementNode, ax_snapshot: str) -> TargetDescriptor:
    descriptor = TargetDescriptor(
        role=node.role,
        accessible_name=node.name or None,
        name_match=NameMatch.exact,
        label=node.label,
        nearby_text=[],
        container=None,
        ordinal=None,
        frame_path=node.frame_path,
        evidence=EvidenceRef(ax_snapshot=ax_snapshot, screenshot=None),
    )
    for enrich in (_with_container, _with_nearby, _with_ordinal):
        outcome = _lands(observation, descriptor, node)
        if outcome is None:
            return descriptor
        descriptor = enrich(observation, descriptor, node, outcome)
    outcome = _lands(observation, descriptor, node)
    if outcome is None:
        return descriptor
    raise DerivationFailed(node, f"no signal set resolves to it: {outcome}")


def _lands(observation: Observation, d: TargetDescriptor, node: ElementNode) -> Exception | None:
    """None when the descriptor resolves strictly to `node`; else the reason."""
    try:
        resolution = resolve(observation, d, strict=True)
    except Ambiguous as exc:
        return exc
    except Unresolvable as exc:
        raise DerivationFailed(node, str(exc)) from exc
    if resolution.node.node_id != node.node_id:
        raise DerivationFailed(node, f"resolves to {resolution.node.node_id} instead")
    return None


def _with_container(
    observation: Observation, d: TargetDescriptor, node: ElementNode, _: Exception
) -> TargetDescriptor:
    for ancestor in observation.ancestors(node):
        if ancestor.role in CONTAINER_ROLES and ancestor.name:
            hint = ContainerHint(role=ancestor.role, accessible_name=ancestor.name)
            return d.model_copy(update={"container": hint})
    return d


def _with_nearby(
    observation: Observation, d: TargetDescriptor, node: ElementNode, _: Exception
) -> TargetDescriptor:
    preceding = [
        other
        for other in observation.nodes
        if other.frame_path == node.frame_path
        and other.role in LANDMARK_TEXT_ROLES
        and other.text
        and 0 < node.order - other.order <= NEARBY_WINDOW
    ]
    if not preceding:
        return d
    nearest = max(preceding, key=lambda n: n.order)
    return d.model_copy(update={"nearby_text": [nearest.text]})


def _with_ordinal(
    observation: Observation, d: TargetDescriptor, node: ElementNode, outcome: Exception
) -> TargetDescriptor:
    assert isinstance(outcome, Ambiguous)
    survivors = {c["node_id"] for c in outcome.candidates}
    ids = [n.node_id for n in sorted(observation.nodes, key=lambda n: n.order) if n.node_id in survivors]
    if node.node_id not in ids:
        raise DerivationFailed(node, "not among the ambiguous survivors")
    return d.model_copy(update={"ordinal": ids.index(node.node_id)})


def to_predicate(
    expect: Expect,
    before: Observation,
    after: Observation | None,
    ax_snapshot: str,
) -> Predicate | None:
    """`None` means "cannot be decided yet": an `element` expectation whose page
    has not appeared. The caller keeps polling."""
    if isinstance(expect, ExpectLocation):
        return LocationMatches(kind="location_matches", pattern=expect.pattern)
    if isinstance(expect, ExpectText):
        return TextPresent(kind="text_present", text=expect.text, scope=None, frame_path=expect.frame_path)
    if isinstance(expect, ExpectValue):
        node = before.by_id(expect.node_id)
        if node is None:
            raise KeyError(expect.node_id)
        return ValueEquals(
            kind="value_equals", target=describe(before, node, ax_snapshot), expected=expect.expected
        )
    if isinstance(expect, ExpectElement):
        if after is None:
            return None
        matches = [n for n in after.nodes if element_matches(n, expect)]
        if not matches:
            return None
        if len(matches) > 1:
            raise AmbiguousExpectation(expect, len(matches))
        return ElementPresent(kind="element_present", target=describe(after, matches[0], ax_snapshot))
    raise TypeError(f"unknown expectation {type(expect).__name__}")


def element_matches(node: ElementNode, expect: ExpectElement) -> bool:
    if node.frame_path != expect.frame_path or node.role != expect.role:
        return False
    if expect.accessible_name is not None and not name_matches(
        node.name, expect.accessible_name, expect.name_match
    ):
        return False
    if expect.label is not None and (
        node.label is None or normalize(node.label) != normalize(expect.label)
    ):
        return False
    return True
