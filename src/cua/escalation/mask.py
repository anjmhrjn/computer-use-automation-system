"""Which nodes a screenshot must paint over: exactly those whose text the run's
redactor would rewrite in the AX snapshot, so pixel evidence has the same coverage
as text evidence by the same rule. Only the innermost such nodes are kept — a
container's subtree text contains everything below it, and masking the root would
black out the page."""

from __future__ import annotations

from cua.policy import Redactor
from cua.surface import ElementNode, Observation


def minimal_masks(observation: Observation, redactor: Redactor) -> list[ElementNode]:
    sensitive = {n.node_id for n in observation.nodes if _rewrites(n, redactor)}
    if not sensitive:
        return []
    innermost = set(sensitive)
    for node in observation.nodes:
        if node.node_id in sensitive:
            for ancestor in observation.ancestors(node):
                innermost.discard(ancestor.node_id)
    return [n for n in observation.nodes if n.node_id in innermost]


def _rewrites(node: ElementNode, redactor: Redactor) -> bool:
    for part in (node.name, node.value, node.label, node.text):
        if part and redactor.redact(part) != part:
            return True
    return False
