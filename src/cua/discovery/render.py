"""Renders an Observation as the text the model reads: one line per node, indented
by depth, carrying the `node_id` the model answers with.

Chrome's layout-table roles are chrome by definition and are dropped unless they
are leaves (a leaf layout cell is where legacy pages keep their data); so is any
node with nothing of its own to say (no name, label, or value, and its text is
only its descendants'). Text is shown on leaves only, so a container never repeats
the page below it. Dropped nodes' descendants are kept, one level up."""

from __future__ import annotations

from cua.surface import ElementNode, Observation

_TEXT_ROLES = {"StaticText", "text"}
_LAYOUT_PREFIX = "LayoutTable"
MAX_TEXT = 120


def render(observation: Observation) -> str:
    children = _child_counts(observation)
    shown = {n.node_id for n in observation.nodes if _worth_showing(n, children)}
    depth = _depths(observation, shown)
    lines = [f"location: {observation.location}"]
    for node in observation.nodes:
        if node.node_id in shown:
            lines.append("  " * depth[node.node_id] + _line(node, leaf=children[node.node_id] == 0))
    return "\n".join(lines)


def _worth_showing(node: ElementNode, children: dict[str, int]) -> bool:
    leaf = children[node.node_id] == 0
    if node.role in _TEXT_ROLES or (node.role.startswith(_LAYOUT_PREFIX) and not leaf):
        return False
    if node.name or node.label or node.value:
        return True
    return bool(node.text) and leaf


def _line(node: ElementNode, *, leaf: bool) -> str:
    parts = [f"[{node.node_id}] {node.role}"]
    if node.name:
        parts.append(f"name={node.name!r}")
    if node.label and node.label != node.name:
        parts.append(f"label={node.label!r}")
    if node.value:
        parts.append(f"value={node.value!r}")
    if leaf and node.text and node.text != node.name and node.text != node.value:
        text = node.text if len(node.text) <= MAX_TEXT else node.text[: MAX_TEXT - 1] + "…"
        parts.append(f"text={text!r}")
    if node.frame_path:
        parts.append(f"frame={node.frame_path}")
    return " ".join(parts)


def _child_counts(observation: Observation) -> dict[str, int]:
    counts = {n.node_id: 0 for n in observation.nodes}
    for node in observation.nodes:
        if node.role in _TEXT_ROLES:
            continue  # a StaticText child is the parent's own text, not a child line
        if node.parent_id in counts:
            counts[node.parent_id] += 1
    return counts


def _depths(observation: Observation, shown: set[str]) -> dict[str, int]:
    """Depth counts only shown ancestors, so dropped wrappers do not indent."""
    index = {n.node_id: n for n in observation.nodes}
    depth: dict[str, int] = {}
    for node in observation.nodes:
        d = 0
        parent_id = node.parent_id
        while parent_id is not None and parent_id in index:
            if parent_id in shown:
                d += 1
            parent_id = index[parent_id].parent_id
        depth[node.node_id] = d
    return depth
