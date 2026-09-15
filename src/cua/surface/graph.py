"""The element graph a Surface reports. Nothing here is web-specific: no DOM node,
no backend id, no frame id. `node_id` is opaque and valid only for the Observation
it came from; the adapter keeps the mapping back to its own handles privately."""

from __future__ import annotations

from cua.schema import StrictModel


class ElementNode(StrictModel):
    node_id: str
    role: str
    name: str
    value: str | None
    text: str
    label: str | None
    frame_path: list[str]
    parent_id: str | None
    order: int


class Observation(StrictModel):
    location: str
    nodes: list[ElementNode]

    def by_id(self, node_id: str) -> ElementNode | None:
        for node in self.nodes:
            if node.node_id == node_id:
                return node
        return None

    def ancestors(self, node: ElementNode) -> list[ElementNode]:
        """Nearest first. Never crosses a frame boundary: a frame root has no parent."""
        index = {n.node_id: n for n in self.nodes}
        chain: list[ElementNode] = []
        current = node
        while current.parent_id is not None:
            parent = index.get(current.parent_id)
            if parent is None:
                break
            chain.append(parent)
            current = parent
        return chain
