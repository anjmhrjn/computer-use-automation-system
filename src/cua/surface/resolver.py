"""Turns a TargetDescriptor into exactly one node of an Observation.

Cumulative narrowing: start with every node of the descriptor's role inside its
frame, then intersect with each recorded signal in ranked order until one node is
left. A signal that would leave zero is skipped as stale (a renamed label should not
kill a target the other signals still pin down). More than one after every signal is
`Ambiguous`, never "the first one" (invariant 3).

`frame_path` is a scope, not a tier: it never falls through to other frames, because
the same label in a different frame is a different control.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from cua.schema import ContainerHint, NameMatch, TargetDescriptor

from .errors import Ambiguous, TierTrace, Unresolvable
from .graph import ElementNode, Observation

NEARBY_WINDOW = 10

_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    return _WS.sub(" ", text).strip().rstrip(":").strip().casefold()


def name_matches(actual: str, expected: str, mode: NameMatch) -> bool:
    if mode is NameMatch.exact:
        return actual == expected
    if mode is NameMatch.contains:
        return expected in actual
    return normalize(actual) == normalize(expected)


@dataclass(frozen=True)
class Resolution:
    node: ElementNode
    tier: str
    trace: list[TierTrace]


Filter = Callable[[ElementNode], bool]


def _container_filter(observation: Observation, hint: ContainerHint) -> Filter:
    def inside(node: ElementNode) -> bool:
        for ancestor in observation.ancestors(node):
            if ancestor.role != hint.role:
                continue
            if hint.accessible_name is None or ancestor.name == hint.accessible_name:
                return True
        return False

    return inside


def _nearby_filter(observation: Observation, texts: list[str]) -> Filter:
    wanted = [normalize(t) for t in texts]

    def near(node: ElementNode) -> bool:
        neighbours = [
            normalize(other.text)
            for other in observation.nodes
            if other.frame_path == node.frame_path
            and abs(other.order - node.order) <= NEARBY_WINDOW
        ]
        return all(any(w in n for n in neighbours) for w in wanted)

    return near


def _tiers(observation: Observation, d: TargetDescriptor) -> list[tuple[str, Filter]]:
    tiers: list[tuple[str, Filter]] = []
    if d.accessible_name is not None:
        expected, mode = d.accessible_name, d.name_match
        tiers.append(("name", lambda n: name_matches(n.name, expected, mode)))
    if d.container is not None:
        tiers.append(("container", _container_filter(observation, d.container)))
    if d.label is not None:
        label = normalize(d.label)
        tiers.append(("label", lambda n: n.label is not None and normalize(n.label) == label))
    if d.nearby_text:
        tiers.append(("nearby_text", _nearby_filter(observation, d.nearby_text)))
    return tiers


def resolve(observation: Observation, descriptor: TargetDescriptor) -> Resolution:
    trace: list[TierTrace] = []
    candidates = [
        n
        for n in observation.nodes
        if n.frame_path == descriptor.frame_path and n.role == descriptor.role
    ]
    trace.append(TierTrace("frame+role", len(observation.nodes), len(candidates), False))
    if not candidates:
        raise Unresolvable(
            f"no {descriptor.role!r} in frame {descriptor.frame_path}", descriptor, trace, []
        )
    if len(candidates) == 1:
        return Resolution(candidates[0], "frame+role", trace)

    for tier, keep in _tiers(observation, descriptor):
        narrowed = [n for n in candidates if keep(n)]
        if not narrowed:
            trace.append(TierTrace(tier, len(candidates), 0, True))
            continue
        trace.append(TierTrace(tier, len(candidates), len(narrowed), False))
        candidates = narrowed
        if len(candidates) == 1:
            return Resolution(candidates[0], tier, trace)

    if descriptor.ordinal is not None:
        ordered = sorted(candidates, key=lambda n: n.order)
        trace.append(TierTrace("ordinal", len(candidates), 1, False))
        if descriptor.ordinal >= len(ordered):
            raise Unresolvable(
                f"ordinal {descriptor.ordinal} out of range for {len(ordered)} candidates",
                descriptor,
                trace,
                ordered,
            )
        return Resolution(ordered[descriptor.ordinal], "ordinal", trace)

    raise Ambiguous(
        f"{len(candidates)} nodes match {descriptor.role!r} after every signal",
        descriptor,
        trace,
        candidates,
    )
