from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import Field

from .common import StrictModel, ValueRef
from .target import ContainerHint, TargetDescriptor


class ElementPresent(StrictModel):
    kind: Literal["element_present"]
    target: TargetDescriptor


class ElementAbsent(StrictModel):
    kind: Literal["element_absent"]
    target: TargetDescriptor


class TextPresent(StrictModel):
    kind: Literal["text_present"]
    text: str
    scope: ContainerHint | None
    frame_path: list[str]


class LocationMatches(StrictModel):
    kind: Literal["location_matches"]
    pattern: str


class ValueEquals(StrictModel):
    kind: Literal["value_equals"]
    target: TargetDescriptor
    expected: ValueRef


Predicate = Annotated[
    Union[ElementPresent, ElementAbsent, TextPresent, LocationMatches, ValueEquals],
    Field(discriminator="kind"),
]
