from __future__ import annotations

import json
from typing import Any

from .capability import Capability


def capability_json_schema() -> dict[str, Any]:
    return Capability.model_json_schema()


def dumps() -> str:
    return json.dumps(capability_json_schema(), indent=2, sort_keys=True) + "\n"
