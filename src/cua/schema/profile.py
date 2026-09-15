"""Per-app knowledge that is not a property of any one capability: the interstitial
pages the app can put in front of a step, and the locations automation may act on.
Kept out of the artifact so every capability for the app shares one declaration and
so an artifact cannot allowlist itself, and out of code so the engine holds no
app-specific text."""

from __future__ import annotations

from typing import Literal

from pydantic import model_validator

from .common import StrictModel
from .predicate import Predicate
from .result import FailureKind
from .target import TargetDescriptor


class Interstitial(StrictModel):
    """`dismiss` is the control that clears the page; `None` means there is nothing
    automation can do, and `failure_kind` is what the run reports. For a dismissable
    interstitial `failure_kind` is reported only when dismissal stops working."""

    name: str
    detector: Predicate
    failure_kind: FailureKind
    dismiss: TargetDescriptor | None


class AppProfile(StrictModel):
    """`allowed_locations` are fnmatch patterns over the opaque location string the
    surface reports; an action is dispatched only while the surface is at one of
    them (or, for a navigate, only towards one of them)."""

    schema_version: Literal["1.0"]
    app_id: str
    allowed_locations: list[str]
    interstitials: list[Interstitial]

    @model_validator(mode="after")
    def _check(self) -> "AppProfile":
        names = [i.name for i in self.interstitials]
        if len(names) != len(set(names)):
            raise ValueError("interstitial names must be unique")
        if not self.allowed_locations:
            raise ValueError("allowed_locations must name at least one location pattern")
        return self
