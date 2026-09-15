"""Evaluates an artifact predicate against one Observation. Pure: no waiting, no
acting. The engine owns the poll loop.

Targets resolve strictly here: a predicate asks whether the element *as recorded*
is on screen, so a signal that no longer matches is a "no", not a skip."""

from __future__ import annotations

from fnmatch import fnmatchcase

from cua.schema import (
    ElementAbsent,
    ElementPresent,
    LocationMatches,
    Predicate,
    TextPresent,
    ValueEquals,
)
from cua.surface import Ambiguous, Observation, Unresolvable, normalize, resolve, within

from .session import Session


def holds(predicate: Predicate, observation: Observation, session: Session | None) -> bool:
    """`session` is only consulted for a `value_equals` parameter lookup; a caller
    with no run (the compiler cross-checking a saved snapshot) passes `None` and
    gets an error, not a guess, if the predicate needs one."""
    if isinstance(predicate, ElementPresent):
        # Ambiguous is not present: two matches is not "the element" (invariant 3).
        try:
            resolve(observation, predicate.target, strict=True)
        except (Unresolvable, Ambiguous):
            return False
        return True
    if isinstance(predicate, ElementAbsent):
        try:
            resolve(observation, predicate.target, strict=True)
        except Unresolvable:
            return True
        except Ambiguous:
            return False
        return False
    if isinstance(predicate, TextPresent):
        wanted = normalize(predicate.text)
        keep = within(observation, predicate.scope) if predicate.scope else None
        return any(
            wanted in normalize(node.text)
            for node in observation.nodes
            if node.frame_path == predicate.frame_path and (keep is None or keep(node))
        )
    if isinstance(predicate, LocationMatches):
        return fnmatchcase(observation.location, predicate.pattern)
    if isinstance(predicate, ValueEquals):
        try:
            resolution = resolve(observation, predicate.target, strict=True)
        except (Unresolvable, Ambiguous):
            return False
        if session is None:
            raise TypeError("value_equals needs a session to look up the expected value")
        return resolution.node.value == session.value(predicate.expected)
    raise TypeError(f"unknown predicate {type(predicate).__name__}")


def describe(predicate: Predicate) -> str:
    if isinstance(predicate, (ElementPresent, ElementAbsent)):
        target = predicate.target
        who = target.accessible_name or target.label or "?"
        state = "present" if isinstance(predicate, ElementPresent) else "absent"
        return f"{target.role} {who!r} {state} in frame {target.frame_path}"
    if isinstance(predicate, TextPresent):
        return f"text {predicate.text!r} present in frame {predicate.frame_path}"
    if isinstance(predicate, LocationMatches):
        return f"location matches {predicate.pattern!r}"
    if isinstance(predicate, ValueEquals):
        who = predicate.target.accessible_name or predicate.target.label or "?"
        # Parameter values are named, never inlined (invariant 7).
        expected = (
            f"parameter {predicate.expected.name}"
            if predicate.expected.kind == "parameter"
            else repr(predicate.expected.text)
        )
        return f"{predicate.target.role} {who!r} has value {expected}"
    raise TypeError(f"unknown predicate {type(predicate).__name__}")
