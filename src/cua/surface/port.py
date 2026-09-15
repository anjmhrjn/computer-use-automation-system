from __future__ import annotations

from typing import Protocol

from .actions import SurfaceAction
from .graph import ElementNode, Observation


class Surface(Protocol):
    """Anything we can perceive and act on.

    `observe` is a snapshot, not a wait; waiting for a state predicate is the caller's
    loop. `act` performs exactly one action: `Navigate` takes no node, everything
    else takes a node from the *latest* observation. Returns the text for `ReadText`,
    `None` otherwise. `capture` returns a PNG of the surface with every node in
    `mask` painted over; a node it cannot cover is an error, never a partial image.
    """

    def observe(self) -> Observation: ...

    def act(self, action: SurfaceAction, node: ElementNode | None) -> str | None: ...

    def capture(self, mask: list[ElementNode]) -> bytes: ...
