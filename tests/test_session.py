"""Every action the engine issues reaches the surface through `Session.act`,
and only there (invariant 4). A fake surface records what it is asked."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cua.replay import MissingParameter, Session, replay
from cua.schema import Capability
from cua.surface import ElementNode, Navigate, Observation, ReadText, SurfaceAction, TypeText

from .fixtures import profile, record_frame, search_page, with_value

ARTIFACT = Path(__file__).resolve().parents[1] / "artifacts" / "lookup_member_status.json"


class ScriptedSurface:
    """Serves the search page, then the same page with the id typed, then the record."""

    def __init__(self) -> None:
        self.acted: list[tuple[SurfaceAction, str | None]] = []
        self.observed = 0
        self._typed = ""

    def observe(self) -> Observation:
        self.observed += 1
        kinds = [type(a) for a, _ in self.acted]
        if kinds.count(Navigate) == 0:
            return Observation(location="/", nodes=[])
        if not any(k.__name__ == "Click" for k in kinds):
            return with_value(search_page(), "box", self._typed)
        return record_frame()

    def act(self, action: SurfaceAction, node: ElementNode | None) -> str | None:
        self.acted.append((action, node.node_id if node else None))
        if isinstance(action, TypeText):
            self._typed = action.text
        if isinstance(action, ReadText) and node is not None:
            return node.text
        return None


@pytest.fixture
def capability() -> Capability:
    return Capability.model_validate(json.loads(ARTIFACT.read_text()))


def test_every_action_passes_through_session_act(capability: Capability, monkeypatch: pytest.MonkeyPatch) -> None:
    surface = ScriptedSurface()
    seen: list[SurfaceAction] = []
    original = Session.act

    def spy(self: Session, action: SurfaceAction, node: ElementNode | None) -> str | None:
        seen.append(action)
        return original(self, action, node)

    monkeypatch.setattr(Session, "act", spy)
    result = replay(capability, {"member_id": "10001"}, surface, profile())

    assert [a for a, _ in surface.acted] == seen
    assert [type(a).__name__ for a in seen] == ["Navigate", "TypeText", "Click", "ReadText", "ReadText"]
    assert result.status.value == "success" and result.outcome == "found"
    assert result.outputs == {"plan_status": "Active", "renewal_date": "2027-01-15"}
    assert result.failure is None
    assert [t.resolved_tier for t in result.steps] == [None, "name", "container", "label", "label"]


def test_parameter_value_reaches_surface_but_not_result(capability: Capability) -> None:
    surface = ScriptedSurface()
    result = replay(capability, {"member_id": "10001"}, surface, profile())
    typed = [a for a, _ in surface.acted if isinstance(a, TypeText)]
    assert typed == [TypeText("10001")]
    assert "10001" not in result.model_dump_json()


def test_missing_parameter_is_rejected_before_any_observation(capability: Capability) -> None:
    surface = ScriptedSurface()
    with pytest.raises(MissingParameter) as info:
        replay(capability, {}, surface, profile())
    assert "member_id" in info.value.expected
    assert surface.observed == 0


def test_unknown_parameter_is_rejected(capability: Capability) -> None:
    with pytest.raises(MissingParameter) as info:
        replay(capability, {"member_id": "1", "ssn": "x"}, ScriptedSurface(), profile())
    assert "ssn" in info.value.observed
