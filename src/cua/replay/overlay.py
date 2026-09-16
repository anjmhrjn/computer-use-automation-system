"""Rewrites a Capability's descriptor signals for one tenant. Rules only, no model,
and only the fields the resolver and the predicates read: a rename never reaches a
step id, an intent, a location, or a parameter name, even when those happen to
contain the same text. A base string is renamed only when it matches a key whole;
partial matches are left alone because `name_match: contains` already handles
substrings at resolution time and rewriting inside them would be a second,
invisible matcher."""

from __future__ import annotations

from dataclasses import dataclass

from cua.schema import (
    Capability,
    ContainerHint,
    ElementAbsent,
    ElementPresent,
    LocationMatches,
    OutcomeSpec,
    Predicate,
    Step,
    TargetDescriptor,
    TenantOverlay,
    TextPresent,
    ValueEquals,
)


class OverlayError(Exception):
    def __init__(self, expected: str, observed: str) -> None:
        self.expected = expected
        self.observed = observed
        super().__init__(f"expected {expected}; observed {observed}")


@dataclass(frozen=True)
class Applied:
    tenant_id: str
    applied: tuple[str, ...]
    unused: tuple[str, ...]


class _Map:
    def __init__(self, renames: dict[str, str]) -> None:
        self._renames = renames
        self.hits: set[str] = set()

    def one(self, text: str) -> str:
        if text not in self._renames:
            return text
        self.hits.add(text)
        return self._renames[text]

    def maybe(self, text: str | None) -> str | None:
        return None if text is None else self.one(text)

    def many(self, texts: list[str]) -> list[str]:
        return [self.one(t) for t in texts]

    def unused(self) -> set[str]:
        return set(self._renames) - self.hits


class _Renamer:
    def __init__(self, overlay: TenantOverlay) -> None:
        self.names = _Map(overlay.renames)
        self.frames = _Map(overlay.frames)

    def container(self, hint: ContainerHint | None) -> ContainerHint | None:
        if hint is None:
            return None
        return hint.model_copy(update={"accessible_name": self.names.maybe(hint.accessible_name)})

    def target(self, d: TargetDescriptor | None) -> TargetDescriptor | None:
        if d is None:
            return None
        return d.model_copy(
            update={
                "accessible_name": self.names.maybe(d.accessible_name),
                "label": self.names.maybe(d.label),
                "nearby_text": self.names.many(d.nearby_text),
                "container": self.container(d.container),
                "frame_path": self.frames.many(d.frame_path),
            }
        )

    def predicate(self, p: Predicate) -> Predicate:
        if isinstance(p, (ElementPresent, ElementAbsent, ValueEquals)):
            return p.model_copy(update={"target": self.target(p.target)})
        if isinstance(p, TextPresent):
            return p.model_copy(
                update={
                    "text": self.names.one(p.text),
                    "scope": self.container(p.scope),
                    "frame_path": self.frames.many(p.frame_path),
                }
            )
        if isinstance(p, LocationMatches):
            return p
        raise TypeError(f"unhandled predicate {type(p).__name__}")

    def step(self, s: Step) -> Step:
        return s.model_copy(
            update={"target": self.target(s.target), "postcondition": self.predicate(s.postcondition)}
        )

    def outcome(self, o: OutcomeSpec) -> OutcomeSpec:
        return o.model_copy(update={"detector": self.predicate(o.detector)})


def apply_overlay(capability: Capability, overlay: TenantOverlay) -> tuple[Capability, Applied]:
    if overlay.app_id != capability.target.app_id:
        raise OverlayError(
            f"an overlay for app {capability.target.app_id!r}",
            f"tenant {overlay.tenant_id!r} overlay declares app_id {overlay.app_id!r}",
        )
    r = _Renamer(overlay)
    rewritten = capability.model_copy(
        update={
            "steps": [r.step(s) for s in capability.steps],
            "outcomes": [r.outcome(o) for o in capability.outcomes],
            "checkpoint": r.predicate(capability.checkpoint),
        }
    )
    # model_copy skips validation; re-validate so a rewrite can never leave an
    # artifact the loader would reject.
    rewritten = Capability.model_validate(rewritten.model_dump())
    applied = tuple(sorted(r.names.hits)) + tuple(f"frame:{f}" for f in sorted(r.frames.hits))
    unused = tuple(sorted(r.names.unused())) + tuple(f"frame:{f}" for f in sorted(r.frames.unused()))
    return rewritten, Applied(overlay.tenant_id, applied, unused)
