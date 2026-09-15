from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_SEED = Path(__file__).with_name("members.json")


@dataclass(frozen=True)
class Member:
    member_id: str
    name: str
    plan_status: str
    renewal_date: str
    restricted: bool


def load_members() -> dict[str, Member]:
    raw = json.loads(_SEED.read_text())
    members = {row["member_id"]: Member(**row) for row in raw}
    if len(members) != len(raw):
        raise ValueError("duplicate member_id in members.json")
    return members
