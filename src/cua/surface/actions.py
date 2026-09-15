"""Actions as the Surface sees them: concrete values only.

The artifact's actions reference parameters by name (`ValueRef`). Turning a name into
a value is the replay engine's job, so by the time an action reaches the port the
value is a plain string. Keeping the two types separate means an adapter can never
see a parameter table, and the artifact can never hold a value."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union


@dataclass(frozen=True)
class Navigate:
    location: str


@dataclass(frozen=True)
class Click:
    pass


@dataclass(frozen=True)
class TypeText:
    text: str


@dataclass(frozen=True)
class SelectOption:
    option: str


@dataclass(frozen=True)
class ReadText:
    pass


SurfaceAction = Union[Navigate, Click, TypeText, SelectOption, ReadText]
