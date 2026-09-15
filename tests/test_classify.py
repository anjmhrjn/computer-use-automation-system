"""Each detector maps to the right class against fixture pages, and each engine
error maps to the right failure kind. No surface, no app."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from cua.replay import (
    CheckpointFailed,
    InterstitialDetected,
    PostconditionTimeout,
    Session,
    TargetUnresolved,
    screen,
    to_failure,
)
from cua.schema import AppProfile, FailureKind, ReplayResult, ReplayStatus
from cua.surface import ElementNode, Observation, StaleNode, SurfaceNotReady, Unresolvable

from .fixtures import (
    descriptor,
    forbidden_page,
    maintenance_page,
    node,
    not_found_page,
    profile,
    record_frame,
    search_page,
    session,
    session_expired_page,
)


class NoSurface:
    def observe(self) -> Observation:
        raise AssertionError("classification observed")

    def act(self, action: object, node: ElementNode | None) -> str | None:
        raise AssertionError("classification acted")


def _session() -> Session:
    return session(NoSurface())


@pytest.mark.parametrize(
    ("page", "expected"),
    [
        (session_expired_page(), "session_expired"),
        (maintenance_page(), "maintenance_notice"),
        (forbidden_page(), "access_denied"),
        (not_found_page(), None),  # a business outcome, not an interstitial
        (search_page(), None),
        (record_frame(), None),
    ],
)
def test_screen_names_the_interstitial_or_nothing(page: Observation, expected: str | None) -> None:
    found = screen(page, profile(), _session())
    assert (found.name if found else None) == expected


def test_profile_classes_and_dismissability() -> None:
    by_name = {i.name: i for i in profile().interstitials}
    assert by_name["maintenance_notice"].dismiss is not None
    assert by_name["maintenance_notice"].failure_kind is FailureKind.interstitial_persisted
    assert by_name["session_expired"].dismiss is None
    assert by_name["session_expired"].failure_kind is FailureKind.session_expired
    assert by_name["access_denied"].dismiss is None
    assert by_name["access_denied"].failure_kind is FailureKind.permission_denied


def test_profile_rejects_duplicate_names() -> None:
    raw = json.loads(profile().model_dump_json())
    raw["interstitials"].append(raw["interstitials"][0])
    with pytest.raises(ValidationError, match="unique"):
        AppProfile.model_validate(raw)


def _unresolvable() -> Unresolvable:
    return Unresolvable("no match", descriptor("button", "Nope"), [], [])


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (PostconditionTimeout("s", "x", "y"), FailureKind.timeout),
        (TargetUnresolved("s", _unresolvable()), FailureKind.target_drift),
        (CheckpointFailed("s", "x", "y"), FailureKind.checkpoint_failed),
        (StaleNode(node("n", "button", "Go")), FailureKind.surface_error),
        (SurfaceNotReady("still loading"), FailureKind.surface_error),
    ],
)
def test_errors_map_to_failure_kinds(exc: Exception, kind: FailureKind) -> None:
    failure = to_failure(exc, "s")  # type: ignore[arg-type]
    assert failure.kind is kind
    assert failure.step_id == "s"
    assert failure.expected and failure.observed


def test_interstitial_failure_takes_its_kind_from_the_profile() -> None:
    by_name = {i.name: i for i in profile().interstitials}
    expired = to_failure(InterstitialDetected("open_search", by_name["session_expired"], 0), "x")
    assert expired.kind is FailureKind.session_expired
    assert expired.step_id == "open_search"
    stuck = to_failure(InterstitialDetected("open_search", by_name["maintenance_notice"], 2), "x")
    assert stuck.kind is FailureKind.interstitial_persisted
    assert "2 dismissal" in stuck.observed


def test_result_shape_is_enforced() -> None:
    base = dict(capability_id="c", version="1", outputs={}, steps=[], interventions=[])
    ReplayResult(status=ReplayStatus.success, outcome="found", failure=None, **base)
    with pytest.raises(ValidationError, match="imply each other"):
        ReplayResult(status=ReplayStatus.failed, outcome="found", failure=None, **base)
    with pytest.raises(ValidationError, match="exactly when"):
        ReplayResult(status=ReplayStatus.success, outcome=None, failure=None, **base)
