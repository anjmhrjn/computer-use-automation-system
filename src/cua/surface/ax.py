"""Reads fields off raw CDP `Accessibility.getFullAXTree` nodes. Chrome's shape,
kept out of the adapter's control flow: `name.sources` lists every candidate and the
one that applied carries a `value`; a `definition` has no accessible name and is
labelled by the preceding `term` sibling."""

from __future__ import annotations

import re
from typing import Any

_WS = re.compile(r"\s+")
_LABEL_SOURCES = {"labelfor", "label", "labelwrapped"}


def value(field: dict[str, Any] | None) -> str:
    if not field:
        return ""
    raw = field.get("value")
    return str(raw) if raw is not None else ""


def text(ax: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> str:
    if "value" in ax and value(ax["value"]):
        return value(ax["value"])
    parts: list[str] = []

    def collect(n: dict[str, Any]) -> None:
        if value(n.get("role")) == "StaticText":
            parts.append(value(n.get("name")))
            return
        for child in n.get("childIds", []):
            if child in by_id:
                collect(by_id[child])

    collect(ax)
    return _WS.sub(" ", " ".join(parts)).strip()


def label(ax: dict[str, Any]) -> str | None:
    for source in ax.get("name", {}).get("sources", []):
        if source.get("type") != "relatedElement" or "value" not in source:
            continue
        if source.get("nativeSource") in _LABEL_SOURCES or source.get("attribute") == "aria-labelledby":
            return value(source["value"]) or None
    return None


def term_for(ax: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> str | None:
    """A `definition` is labelled by the `term` that precedes it in its list."""
    if value(ax.get("role")) != "definition":
        return None
    parent = by_id.get(ax.get("parentId", ""))
    if parent is None:
        return None
    last_term: str | None = None
    for sibling_id in parent.get("childIds", []):
        sibling = by_id.get(sibling_id)
        if sibling is None:
            continue
        if sibling["nodeId"] == ax["nodeId"]:
            return last_term
        if value(sibling.get("role")) == "term":
            last_term = value(sibling.get("name")) or None
    return None
