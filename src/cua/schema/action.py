from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import Field

from .common import StrictModel, ValueRef


class Navigate(StrictModel):
    """Moves the surface to a named location. A location is opaque above the
    Surface port -- the web adapter fills it from the URL path, a desktop adapter
    would use a window title."""

    kind: Literal["navigate"]
    location: str


class Click(StrictModel):
    kind: Literal["click"]


class TypeText(StrictModel):
    kind: Literal["type_text"]
    value: ValueRef


class SelectOption(StrictModel):
    kind: Literal["select_option"]
    option: ValueRef


class ReadText(StrictModel):
    kind: Literal["read_text"]
    bind_to: str


Action = Annotated[
    Union[Navigate, Click, TypeText, SelectOption, ReadText],
    Field(discriminator="kind"),
]
