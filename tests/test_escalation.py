"""A failure the environment caused becomes a human handoff: the token changes
hands, the operator's resolution decides where the run resumes, and what the human
did is recorded through the redactor. Failures the artifact or a guardrail caused
never reach the operator. The scripted escalator *is* the human: it changes the
surface and answers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cua.escalation import ControlHeld, EscalationError, diff, minimal_masks
from cua.policy import Redactor
from cua.replay import ESCALATES, TERMINAL, escalates, replay
from cua.schema import (
    Capability,
    FailureKind,
    InterventionRequest,
    ReplayStatus,
    Resolution,
    ResolutionKind,
    RiskClass,
)
from cua.surface import Click, ElementNode, Navigate, Observation, SurfaceAction, TypeText

from .fixtures import (
    ScriptedSurface,
    capability,
    forbidden_page,
    node,
    profile,
    raw_capability,
    record_frame,
    search_page,
    session,
    session_expired_page,
)

PARAMS = {"member_id": "10001"}


class Operator:
    """Answers with a fixed resolution after running `on_request` (the human's
    actions on the surface). Records every request it saw."""

    def __init__(self, resolution: Resolution, on_request=None) -> None:  # type: ignore[no-untyped-def]
        self.resolution = resolution
        self.on_request = on_request
        self.requests: list[InterventionRequest] = []

    def request(self, request: InterventionRequest) -> Resolution:
        self.requests.append(request)
        if self.on_request is not None:
            self.on_request(request)
        return self.resolution


class ExpiresAfterTyping(ScriptedSurface):
    """The session lapses once, as the id is first typed: the notice replaces the
    response, so `enter_member_id`'s postcondition never holds and the failure lands
    there. A human then has to start over from the search page."""

    def __init__(self) -> None:
        super().__init__()
        self._expired = False

    def act(self, action: SurfaceAction, node: ElementNode | None) -> str | None:
        result = super().act(action, node)
        if isinstance(action, TypeText) and not self._expired:
            self._expired = True
            self.override = session_expired_page()
        return result


def retry(step: str, note: str = "") -> Resolution:
    return Resolution(kind=ResolutionKind.retry, resume_from=step, note=note)


def risky_submit() -> Capability:
    raw = raw_capability()
    next(s for s in raw["steps"] if s["step_id"] == "submit_search")["risk"] = RiskClass.risky.value
    return Capability.model_validate(raw)


def test_session_expired_hands_off_and_resumes_from_the_step_the_operator_names() -> None:
    surface = ExpiresAfterTyping()

    def human_signs_in_again(request: InterventionRequest) -> None:
        assert surface.override is not None
        surface.override = None

    operator = Operator(retry("open_search", "signed in again"), human_signs_in_again)
    result = replay(capability(), PARAMS, surface, profile(), escalator=operator)

    assert result.status is ReplayStatus.success
    assert result.outputs == {"plan_status": "Active", "renewal_date": "2027-01-15"}
    assert [t.step_id for t in result.steps] == [
        "open_search",
        "open_search", "enter_member_id", "submit_search", "read_plan_status", "read_renewal_date",
    ]
    [record] = result.interventions
    assert record.failure_kind is FailureKind.session_expired
    assert record.step_id == "enter_member_id"
    assert record.resolution.resume_from == "open_search"
    assert record.evidence_dir is None

    [request] = operator.requests
    assert request.resumable_steps == ["open_search", "enter_member_id"]
    assert request.inputs == ["member_id"]
    assert request.location == "/members/search"
    assert "10001" not in request.model_dump_json()


def test_approve_runs_the_risky_step_exactly_once() -> None:
    surface = ScriptedSurface()
    operator = Operator(Resolution(kind=ResolutionKind.approve, resume_from=None, note="ok"))
    result = replay(risky_submit(), PARAMS, surface, profile(), escalator=operator)

    assert result.status is ReplayStatus.success
    assert [type(a) for a, _ in surface.acted].count(Click) == 1
    [record] = result.interventions
    assert record.failure_kind is FailureKind.approval_required
    assert record.resolution.kind is ResolutionKind.approve
    # The approval was for one step of one run; the policy itself did not change.
    assert operator.requests[0].failure.kind is FailureKind.approval_required


def test_abort_keeps_the_original_failure_and_records_the_note() -> None:
    surface = ScriptedSurface(final=forbidden_page())
    operator = Operator(Resolution(kind=ResolutionKind.abort, resume_from=None, note="restricted record"))
    result = replay(capability(), {"member_id": "20001"}, surface, profile(), escalator=operator)

    assert result.status is ReplayStatus.failed and result.failure is not None
    assert result.failure.kind is FailureKind.permission_denied
    assert result.failure.step_id == "submit_search"
    [record] = result.interventions
    assert record.resolution.kind is ResolutionKind.abort
    assert record.resolution.note == "restricted record"
    assert [type(a) for a, _ in surface.acted] == [Navigate, TypeText, Click]
    # The denied page sits at /member/<id>; the request leaves the process redacted.
    [request] = operator.requests
    assert request.location == "/member/<param:member_id>"
    assert "20001" not in request.model_dump_json()


def test_operator_input_is_validated() -> None:
    surface = ScriptedSurface(final=forbidden_page())
    with pytest.raises(EscalationError, match="resume_from 'read_plan_status' is not one of"):
        replay(capability(), PARAMS, surface, profile(), escalator=Operator(retry("read_plan_status")))

    surface = ScriptedSurface(final=forbidden_page())
    approve = Resolution(kind=ResolutionKind.approve, resume_from=None, note="")
    with pytest.raises(EscalationError, match="'approve' resolves only approval_required"):
        replay(capability(), PARAMS, surface, profile(), escalator=Operator(approve))


def test_artifact_and_guardrail_failures_never_reach_the_operator() -> None:
    class Never:
        def request(self, request: InterventionRequest) -> Resolution:
            raise AssertionError("escalated a terminal failure")

    raw = raw_capability()
    # The role is the base set the resolver narrows; no link named Search exists.
    next(s for s in raw["steps"] if s["step_id"] == "submit_search")["target"]["role"] = "link"
    drifted = Capability.model_validate(raw)
    result = replay(drifted, PARAMS, ScriptedSurface(), profile(), escalator=Never())
    assert result.failure is not None and result.failure.kind is FailureKind.target_drift
    assert result.interventions == []

    fenced = profile().model_copy(update={"allowed_locations": ["/nowhere"]})
    result = replay(capability(), PARAMS, ScriptedSurface(), fenced, escalator=Never())
    assert result.failure is not None and result.failure.kind is FailureKind.policy_denied
    assert result.interventions == []


def test_every_failure_kind_is_placed_in_the_escalation_table() -> None:
    assert ESCALATES | TERMINAL == set(FailureKind)
    assert not ESCALATES & TERMINAL
    for kind in FailureKind:
        escalates(kind)


def test_automation_cannot_act_while_a_human_holds_control() -> None:
    surface = ScriptedSurface()
    s = session(surface)
    s.token.hand_to_human()
    with pytest.raises(ControlHeld):
        s.act(Navigate("/members/search"), None, RiskClass.read_only)
    assert surface.acted == []
    s.token.return_to_automation()
    s.act(Navigate("/members/search"), None, RiskClass.read_only)
    assert len(surface.acted) == 1


def test_masks_are_the_innermost_nodes_the_redactor_would_rewrite() -> None:
    redactor = Redactor((("member_id", "10001"),))
    page = Observation(
        location="/member/10001",
        nodes=[
            node("root", "RootWebArea", "MemberServe", text="MemberServe Member 10001 Active", order=0),
            node("h", "heading", "Member 10001", parent="root", order=1),
            node("d", "definition", parent="root", label="Plan Status", text="Active", order=2),
        ],
    )
    assert [n.node_id for n in minimal_masks(page, redactor)] == ["h"]
    assert minimal_masks(search_page(), redactor) == []
    # An email arriving from the page, not from a parameter, is masked too.
    page = Observation(location="/", nodes=[node("e", "StaticText", "a@b.co", order=0)])
    assert [n.node_id for n in minimal_masks(page, Redactor(()))] == ["e"]


def test_diff_reports_what_the_human_changed() -> None:
    changes = diff(search_page(), record_frame())
    assert (changes.location_before, changes.location_after) == ("/members/search", "/member/10001")
    assert any(n.role == "heading" and n.name == "Member 10001" for n in changes.added)
    assert any(n.role == "textbox" and n.name == "Member ID" for n in changes.removed)
    assert diff(search_page(), search_page()).added == []


def test_handoff_evidence_is_written_through_the_redactor(tmp_path: Path) -> None:
    surface = ExpiresAfterTyping()

    def human(request: InterventionRequest) -> None:
        surface.override = None

    operator = Operator(retry("open_search", "member 10001 had to sign in again"), human)
    result = replay(capability(), PARAMS, surface, profile(), escalator=operator, evidence_dir=tmp_path)

    [record] = result.interventions
    assert record.evidence_dir is not None
    directory = Path(record.evidence_dir)
    assert directory.parent.parent.parent == tmp_path
    assert sorted(p.name for p in directory.iterdir()) == [
        "after.json", "after.png", "before.json", "before.png", "diff.json", "resolution.json",
    ]
    for name in ("before.json", "after.json", "diff.json", "resolution.json"):
        text = (directory / name).read_text()
        assert "10001" not in text, name
    assert json.loads((directory / "resolution.json").read_text())["note"] == (
        "member <param:member_id> had to sign in again"
    )
    after = json.loads((directory / "after.json").read_text())
    assert after["location"] == "/members/search"
    # The after-state is the search page with the id still typed: that textbox is
    # exactly what the after screenshot masked.
    assert surface.captured[-1] == ["box"]
    assert (directory / "after.png").read_bytes().startswith(b"\x89PNG")
