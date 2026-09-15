"""Pydantic JSON Schema -> OpenAI strict-mode schema. The artifact types are already
authored strict-compatible (invariant 10); two things pydantic emits still differ
from what strict mode accepts, and both are mechanical: discriminated unions come
out as `oneOf` + `discriminator` where strict wants `anyOf`, and `Literal` comes
out as `const` where strict wants `enum`. Anything that is not mechanical --
a missing `additionalProperties: false`, an optional field, a default -- is an
authoring error in the types and raises here rather than being patched."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class NotStrict(ValueError):
    pass


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    return _walk(model.model_json_schema(), "$")


def _walk(node: Any, path: str) -> Any:
    if isinstance(node, list):
        return [_walk(item, f"{path}[{i}]") for i, item in enumerate(node)]
    if not isinstance(node, dict):
        return node

    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in ("title", "discriminator"):
            continue
        if key == "oneOf":
            out["anyOf"] = _walk(value, f"{path}.oneOf")
        elif key == "const":
            out["enum"] = [value]
        elif key == "default":
            raise NotStrict(f"{path}: default values are not allowed in strict mode")
        else:
            out[key] = _walk(value, f"{path}.{key}")

    if out.get("type") == "object" and "properties" in out:
        if out.get("additionalProperties") is not False:
            raise NotStrict(f"{path}: object must set additionalProperties: false")
        keys = list(out["properties"])
        if sorted(out.get("required", [])) != sorted(keys):
            raise NotStrict(f"{path}: every property must be required, got {out.get('required')}")
    return out
