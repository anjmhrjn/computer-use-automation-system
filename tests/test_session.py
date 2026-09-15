"""Every action the engine issues reaches the surface through `Session.act`,
and only there (invariant 4). A fake surface records what it is asked."""

from __future__ import annotations

import pytest

from cua.replay import MissingParameter, Session, replay
from cua.schema import Capability, RiskClass
from cua.surface import ElementNode, SurfaceAction, TypeText

from .fixtures import ScriptedSurface, profile
from .fixtures import capability as load_capability


@pytest.fixture
def capability() -> Capability:
    return load_capability()


def test_every_action_passes_through_session_act(capability: Capability, monkeypatch: pytest.MonkeyPatch) -> None:
    surface = ScriptedSurface()
    seen: list[SurfaceAction] = []
    original = Session.act

    def spy(self: Session, action: SurfaceAction, node: ElementNode | None, risk: RiskClass) -> str | None:
        seen.append(action)
        return original(self, action, node, risk)

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
