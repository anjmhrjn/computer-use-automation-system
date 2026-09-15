"""The chokepoint refuses before the surface sees anything: an action outside the
location allowlist, and a risky-class step without approval (invariants 4, 12).
Denials come back classified on the result, never raised."""

from __future__ import annotations

from cua.replay import replay
from cua.schema import AppProfile, Capability, FailureKind, ReplayStatus
from cua.surface import Click, Navigate, TypeText

from .fixtures import ScriptedSurface, capability, maintenance_page, profile, raw_capability


def _capability(**step_edits: dict[str, object]) -> Capability:
    raw = raw_capability()
    for step in raw["steps"]:
        for key, value in step_edits.get(step["step_id"], {}).items():
            if key == "risk":
                step["risk"] = value
            else:
                step["action"][key] = value
    return Capability.model_validate(raw)


def _profile(allowed: list[str]) -> AppProfile:
    return profile().model_copy(update={"allowed_locations": allowed})


def _kinds(surface: ScriptedSurface) -> list[type]:
    return [type(a) for a, _ in surface.acted]


def test_navigate_outside_allowlist_is_denied_before_any_action() -> None:
    surface = ScriptedSurface()
    capability = _capability(open_search={"location": "/admin"})
    result = replay(capability, {"member_id": "10001"}, surface, profile())

    assert result.status is ReplayStatus.failed and result.failure is not None
    assert result.failure.kind is FailureKind.policy_denied
    assert result.failure.step_id == "open_search"
    assert "/admin" in result.failure.observed
    assert surface.acted == []


def test_action_at_location_outside_allowlist_is_denied() -> None:
    surface = ScriptedSurface()
    result = replay(capability(), {"member_id": "10001"}, surface, _profile(["/members/search"]))

    assert result.failure is not None
    assert result.failure.kind is FailureKind.policy_denied
    assert result.failure.step_id == "read_plan_status"
    assert _kinds(surface) == [Navigate, TypeText, Click]
    # The location the read would have happened at is a parameter value; it is redacted.
    assert "10001" not in result.model_dump_json()
    assert "<param:member_id>" in result.failure.observed


def test_risky_step_halts_without_approval() -> None:
    surface = ScriptedSurface()
    capability = _capability(submit_search={"risk": "risky"})
    result = replay(capability, {"member_id": "10001"}, surface, profile())

    assert result.status is ReplayStatus.failed and result.failure is not None
    assert result.failure.kind is FailureKind.approval_required
    assert result.failure.step_id == "submit_search"
    assert _kinds(surface) == [Navigate, TypeText]
    assert [t.step_id for t in result.steps] == ["open_search", "enter_member_id"]


def test_risky_step_runs_with_approval() -> None:
    surface = ScriptedSurface()
    capability = _capability(submit_search={"risk": "risky"})
    result = replay(capability, {"member_id": "10001"}, surface, profile(), approve_risky=True)

    assert result.status is ReplayStatus.success
    assert result.outputs == {"plan_status": "Active", "renewal_date": "2027-01-15"}


def test_dismiss_click_is_policy_checked() -> None:
    class Blocked(ScriptedSurface):
        def observe(self):  # type: ignore[no-untyped-def]
            super().observe()
            return maintenance_page().model_copy(update={"location": "/notice"})

    surface = Blocked()
    result = replay(capability(), {"member_id": "10001"}, surface, profile())

    # The notice is screened before the navigate is dispatched; the Continue click
    # is then refused at /notice, so nothing at all reaches the surface.
    assert result.failure is not None
    assert result.failure.kind is FailureKind.policy_denied
    assert result.failure.step_id == "open_search"
    assert "/notice" in result.failure.observed
    assert surface.acted == []
    assert result.steps == []
