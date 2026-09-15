"""The single flag saying who may act on a session. Held by one party at a time
and checked by the chokepoint on every action. A violation is a programming error,
not a run failure: the engine is blocked for as long as a human holds the token,
so nothing should ever reach the check while it does."""

from __future__ import annotations

from enum import Enum


class Holder(str, Enum):
    automation = "automation"
    human = "human"


class ControlHeld(RuntimeError):
    def __init__(self, holder: Holder) -> None:
        self.holder = holder
        super().__init__(f"control is held by {holder.value}; automation may not act")


class ControlToken:
    def __init__(self) -> None:
        self._holder = Holder.automation

    @property
    def holder(self) -> Holder:
        return self._holder

    def hand_to_human(self) -> None:
        if self._holder is not Holder.automation:
            raise ControlHeld(self._holder)
        self._holder = Holder.human

    def return_to_automation(self) -> None:
        if self._holder is not Holder.human:
            raise RuntimeError("control is not held by a human; nothing to return")
        self._holder = Holder.automation

    def require_automation(self) -> None:
        if self._holder is not Holder.automation:
            raise ControlHeld(self._holder)
