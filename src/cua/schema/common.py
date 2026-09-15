from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """Base for every artifact type.

    `extra="forbid"` emits `additionalProperties: false`, and no subclass field
    carries a default. Together these keep the generated JSON Schema usable as an
    OpenAI strict-mode tool schema, so discovery reuses these types instead of
    maintaining a parallel definition.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


class RiskClass(str, Enum):
    read_only = "read_only"
    reversible = "reversible"
    risky = "risky"


class ValueType(str, Enum):
    string = "string"
    integer = "integer"
    number = "number"
    boolean = "boolean"
    date = "date"


class Sensitivity(str, Enum):
    """Drives redaction. `none` still never means "safe to inline into a step"."""

    none = "none"
    pii = "pii"
    secret = "secret"


class NameMatch(str, Enum):
    exact = "exact"
    contains = "contains"
    normalized = "normalized"


class ParameterValue(StrictModel):
    kind: Literal["parameter"]
    name: str


class LiteralValue(StrictModel):
    kind: Literal["literal"]
    text: str


ValueRef = Annotated[
    Union[ParameterValue, LiteralValue],
    Field(discriminator="kind"),
]
