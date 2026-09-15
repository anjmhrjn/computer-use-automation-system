"""End-to-end: the hand-written artifact replayed against the real app through the
real adapter. Both declared outcomes, one recovery, and every failure class the
app can produce. Faults are armed out-of-band through the app's control endpoint,
which is what keeps them out of the artifact. Headless."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from urllib.request import Request, urlopen

import pytest
from werkzeug.serving import make_server

from app.server import create_app
from cua.replay import MAX_DISMISSALS, replay
from cua.schema import AppProfile, Capability, FailureKind, ReplayResult, ReplayStatus
from cua.surface import PlaywrightWebSurface

from .fixtures import profile as load_profile

ARTIFACT = Path(__file__).resolve().parents[1] / "artifacts" / "lookup_member_status.json"


@pytest.fixture(scope="module")
def base_url() -> Iterator[str]:
    server = make_server("127.0.0.1", 0, create_app("a"), threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture(scope="module")
def raw() -> dict[str, object]:
    return json.loads(ARTIFACT.read_text())


@pytest.fixture(scope="module")
def capability(raw: dict[str, object]) -> Capability:
    return Capability.model_validate(raw)


@pytest.fixture(scope="module")
def profile() -> AppProfile:
    return load_profile()


@pytest.fixture(autouse=True)
def clear_faults(base_url: str) -> Iterator[None]:
    yield
    urlopen(Request(f"{base_url}/__faults", method="DELETE")).read()


def arm(base_url: str, kind: str, mode: str = "once") -> None:
    body = json.dumps({"kind": kind, "mode": mode}).encode()
    request = Request(
        f"{base_url}/__faults", data=body, method="POST", headers={"content-type": "application/json"}
    )
    assert urlopen(request).status == 201


def run(base_url: str, capability: Capability, profile: AppProfile, member_id: str) -> ReplayResult:
    with PlaywrightWebSurface(base_url, headless=True) as surface:
        return replay(capability, {"member_id": member_id}, surface, profile)


def with_timeout(raw: dict[str, object], step: int, ms: int) -> Capability:
    copy = json.loads(json.dumps(raw))
    copy["steps"][step]["postcondition_timeout_ms"] = ms
    return Capability.model_validate(copy)


def test_found_member(base_url: str, capability: Capability, profile: AppProfile) -> None:
    result = run(base_url, capability, profile, "10001")
    assert result.status is ReplayStatus.success and result.outcome == "found"
    assert result.outputs == {"plan_status": "Active", "renewal_date": "2027-01-15"}
    assert result.failure is None
    assert [t.step_id for t in result.steps] == [s.step_id for s in capability.steps]
    assert [t.resolved_tier for t in result.steps] == [None, "name", "container", "label", "label"]
    assert all(t.elapsed_ms >= 0 and t.recoveries == [] for t in result.steps)


def test_no_such_member_is_an_outcome_not_a_failure(
    base_url: str, capability: Capability, profile: AppProfile
) -> None:
    result = run(base_url, capability, profile, "99999")
    assert result.status is ReplayStatus.business_outcome and result.outcome == "no_such_member"
    assert result.outputs == {}
    assert [t.step_id for t in result.steps] == ["open_search", "enter_member_id", "submit_search"]


def test_maintenance_notice_is_dismissed_and_the_step_rerun(
    base_url: str, capability: Capability, profile: AppProfile
) -> None:
    arm(base_url, "maintenance_notice")
    result = run(base_url, capability, profile, "10001")
    assert result.status is ReplayStatus.success
    recoveries = result.steps[0].recoveries
    assert [(r.interstitial, r.attempt) for r in recoveries] == [("maintenance_notice", 1)]
    assert all(t.recoveries == [] for t in result.steps[1:])


def test_persistent_interstitial_hits_the_ceiling(
    base_url: str, capability: Capability, profile: AppProfile
) -> None:
    arm(base_url, "maintenance_notice", mode="until_cleared")
    result = run(base_url, capability, profile, "10001")
    assert result.status is ReplayStatus.failed and result.failure is not None
    assert result.failure.kind is FailureKind.interstitial_persisted
    assert result.failure.step_id == "open_search"
    assert f"{MAX_DISMISSALS} dismissal" in result.failure.observed
    assert result.steps == []  # the step never completed, so it has no trace


def test_session_expired_is_a_hard_failure(
    base_url: str, capability: Capability, profile: AppProfile
) -> None:
    arm(base_url, "session_expired")
    result = run(base_url, capability, profile, "10001")
    assert result.status is ReplayStatus.failed and result.failure is not None
    assert result.failure.kind is FailureKind.session_expired
    assert result.failure.step_id == "open_search"


def test_restricted_member_is_permission_denied_on_the_submit_step(
    base_url: str, capability: Capability, profile: AppProfile
) -> None:
    result = run(base_url, capability, profile, "20001")
    assert result.status is ReplayStatus.failed and result.failure is not None
    assert result.failure.kind is FailureKind.permission_denied
    assert result.failure.step_id == "submit_search"
    assert [t.step_id for t in result.steps] == ["open_search", "enter_member_id"]


def test_hung_request_times_out_on_the_step_budget(
    base_url: str, raw: dict[str, object], profile: AppProfile
) -> None:
    budget_ms = 1500
    arm(base_url, "timeout")
    started = time.monotonic()
    result = run(base_url, with_timeout(raw, 0, budget_ms), profile, "10001")
    elapsed_ms = (time.monotonic() - started) * 1000
    assert result.status is ReplayStatus.failed and result.failure is not None
    assert result.failure.kind is FailureKind.timeout
    assert result.failure.step_id == "open_search"
    assert "never became ready" in result.failure.observed
    assert elapsed_ms < 10_000, "adapter waited on its own clock, not the step budget"


def test_unmet_postcondition_is_a_timeout_naming_the_step(
    base_url: str, raw: dict[str, object], profile: AppProfile
) -> None:
    broken = json.loads(json.dumps(raw))
    broken["steps"][0]["postcondition"]["target"]["accessible_name"] = "Nope"
    broken["steps"][0]["postcondition_timeout_ms"] = 300
    result = run(base_url, Capability.model_validate(broken), profile, "10001")
    assert result.status is ReplayStatus.failed and result.failure is not None
    assert result.failure.kind is FailureKind.timeout
    assert result.failure.step_id == "open_search"
    assert "Nope" in result.failure.expected
    assert "/members/search" in result.failure.observed


def test_drifted_target_is_target_drift(
    base_url: str, raw: dict[str, object], profile: AppProfile
) -> None:
    broken = json.loads(json.dumps(raw))
    broken["steps"][1]["target"]["role"] = "combobox"
    result = run(base_url, Capability.model_validate(broken), profile, "10001")
    assert result.status is ReplayStatus.failed and result.failure is not None
    assert result.failure.kind is FailureKind.target_drift
    assert result.failure.step_id == "enter_member_id"


def test_hang_after_a_click_is_still_a_timeout(
    base_url: str, raw: dict[str, object], profile: AppProfile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The click is dispatched, then the response never comes. The action must not
    be reported as failed; the step must time out on its own budget."""
    from cua.replay import Session
    from cua.surface import Click

    original = Session.act

    def arm_before_click(self: Session, action: object, node: object, risk: object) -> str | None:
        if isinstance(action, Click):
            arm(base_url, "timeout")
        return original(self, action, node, risk)  # type: ignore[arg-type]

    monkeypatch.setattr(Session, "act", arm_before_click)
    result = run(base_url, with_timeout(raw, 2, 1500), profile, "10001")
    assert result.status is ReplayStatus.failed and result.failure is not None
    assert result.failure.kind is FailureKind.timeout
    assert result.failure.step_id == "submit_search"
    assert [t.step_id for t in result.steps] == ["open_search", "enter_member_id"]
