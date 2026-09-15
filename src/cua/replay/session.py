"""The single chokepoint every action passes through (invariant 4).

Today it forwards to the surface and resolves parameter references. The policy check
(item 6) and the control-token check (item 9) are added inside `act()`; the engine
never holds the surface itself, so there is no second path to bypass them."""

from __future__ import annotations

from cua.schema import LiteralValue, ValueRef
from cua.surface import ElementNode, Observation, Surface, SurfaceAction


class Session:
    def __init__(self, surface: Surface, params: dict[str, str]) -> None:
        self._surface = surface
        self._params = params

    def observe(self) -> Observation:
        return self._surface.observe()

    def act(self, action: SurfaceAction, node: ElementNode | None) -> str | None:
        return self._surface.act(action, node)

    def value(self, ref: ValueRef) -> str:
        if isinstance(ref, LiteralValue):
            return ref.text
        return self._params[ref.name]
