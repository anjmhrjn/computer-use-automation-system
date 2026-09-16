"""A tenant's deviations from the base app, expressed as renames of the user-visible
strings a TargetDescriptor keys on. One file per tenant serves every capability for
the app, so a map may name strings a given capability never uses; the base string
is the key so the overlay reads against the artifact it modifies, not against the
app's source. Frame titles are their own namespace because a frame is a scope, not
a name signal, and a tenant can retitle a frame independently of the heading inside
it (variant-b does)."""

from __future__ import annotations

from typing import Literal

from pydantic import model_validator

from .common import StrictModel


class TenantOverlay(StrictModel):
    schema_version: Literal["1.0"]
    tenant_id: str
    app_id: str
    renames: dict[str, str]
    frames: dict[str, str]

    @model_validator(mode="after")
    def _check(self) -> "TenantOverlay":
        if not self.renames and not self.frames:
            raise ValueError("an overlay must rename at least one base string")
        for field in ("renames", "frames"):
            mapping: dict[str, str] = getattr(self, field)
            identical = sorted(k for k, v in mapping.items() if k == v)
            if identical:
                raise ValueError(f"{field} map strings to themselves: {identical}")
            values = list(mapping.values())
            colliding = sorted({v for v in values if values.count(v) > 1})
            if colliding:
                raise ValueError(f"{field} collapse several base strings onto one: {colliding}")
        return self
