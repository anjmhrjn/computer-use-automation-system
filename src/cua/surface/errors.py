from __future__ import annotations

from dataclasses import dataclass

from cua.schema import TargetDescriptor

from .graph import ElementNode


@dataclass(frozen=True)
class TierTrace:
    tier: str
    before: int
    after: int
    skipped: bool


def summarize(node: ElementNode) -> dict[str, object]:
    return {
        "node_id": node.node_id,
        "role": node.role,
        "name": node.name,
        "label": node.label,
        "frame_path": node.frame_path,
    }


class SurfaceError(Exception):
    pass


class ResolutionError(SurfaceError):
    def __init__(
        self,
        message: str,
        descriptor: TargetDescriptor,
        trace: list[TierTrace],
        candidates: list[ElementNode],
    ) -> None:
        self.descriptor = descriptor
        self.trace = trace
        self.candidates = [summarize(n) for n in candidates]
        tried = " > ".join(
            f"{t.tier}({t.before}->{t.after}{', skipped' if t.skipped else ''})" for t in trace
        )
        super().__init__(f"{message}; tiers: {tried or 'none'}; candidates: {self.candidates}")


class Unresolvable(ResolutionError):
    pass


class Ambiguous(ResolutionError):
    pass


class StaleNode(SurfaceError):
    def __init__(self, node: ElementNode) -> None:
        self.node = node
        super().__init__(
            f"node {node.node_id} ({node.role} {node.name!r}) is not in the latest observation"
        )


class ActionNotApplicable(SurfaceError):
    pass


class SurfaceNotReady(SurfaceError):
    """The surface could not produce a settled observation within its readiness
    bound. Not a failure by itself: the caller's own deadline decides that."""
