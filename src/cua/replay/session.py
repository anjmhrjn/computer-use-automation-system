"""The single chokepoint every action passes through (invariant 4).

`act()` checks the policy before dispatching and logs the action, never its value:
the typed or selected text is the one thing the surface must see and the log must
not. The engine never holds the surface itself, so there is no second path. The
control-token check (item 9) is added here too."""

from __future__ import annotations

from cua.policy import Policy, PolicyError, check
from cua.schema import LiteralValue, RiskClass, ValueRef
from cua.surface import ElementNode, Observation, Surface, SurfaceAction

from .events import EventLog


class Session:
    def __init__(self, surface: Surface, params: dict[str, str], policy: Policy, log: EventLog) -> None:
        self._surface = surface
        self._params = params
        self._policy = policy
        self.log = log
        self._location: str | None = None
        self.step_id = "<start>"

    def observe(self) -> Observation:
        observation = self._surface.observe()
        self._location = observation.location
        return observation

    def act(self, action: SurfaceAction, node: ElementNode | None, risk: RiskClass) -> str | None:
        fields = {
            "step_id": self.step_id,
            "kind": type(action).__name__.lower(),
            "risk": risk.value,
            "location": self._location,
            "target": {"role": node.role, "name": node.name} if node else None,
        }
        try:
            check(action, risk, self._location, self._policy)
        except PolicyError as exc:
            self.log.emit("policy_denied", **fields, expected=exc.expected, observed=exc.observed)
            raise
        self.log.emit("action", **fields)
        return self._surface.act(action, node)

    def value(self, ref: ValueRef) -> str:
        if isinstance(ref, LiteralValue):
            return ref.text
        return self._params[ref.name]
