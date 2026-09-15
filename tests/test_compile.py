"""Compile is rules over a transcript: dead ends pruned, parameters by name,
one success run plus prefix-compatible business runs, and every way a run can
fail to compile named. No browser, no model."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cua.compiler import (
    COMPILER_VERSION,
    DiscoveryRun,
    Incompatible,
    Indiscriminate,
    InlinedValue,
    NotCompilable,
    compile,
    load,
)
from cua.discovery import (
    EXPECT_TIMEOUT_MS,
    Act,
    Contract,
    DeclaredInput,
    DiscoveryStatus,
    ExpectLocation,
    Finish,
    RunFinished,
    RunStarted,
    TurnRecord,
)
from cua.schema import (
    Action,
    Capability,
    Click,
    ContainerHint,
    ElementPresent,
    EvidenceRef,
    LocationMatches,
    NameMatch,
    Navigate,
    OutcomeKind,
    OutputSpec,
    ParameterValue,
    Predicate,
    ReadText,
    RiskClass,
    Sensitivity,
    TargetDescriptor,
    TextPresent,
    TypeText,
    ValueEquals,
    ValueType,
)
from cua.surface import Observation

from .fixtures import ROOT, descriptor, not_found_page, profile, record_frame, search_page

EVIDENCE = ROOT / "evidence"
SUCCESS_RUN = EVIDENCE / "20260915T160505-f09d3b" / "transcript.jsonl"
NOT_FOUND_RUN = EVIDENCE / "20260915T160854-f94d73" / "transcript.jsonl"

MEMBER_ID = ParameterValue(kind="parameter", name="member_id")
FRAME = ["Member Record"]
BOX = descriptor("textbox", "Member ID", label="Member ID")
SEARCH = descriptor("button", "Search", container=ContainerHint(role="form", accessible_name="Member search"))
SITE_SEARCH = descriptor("button", "Search", container=ContainerHint(role="form", accessible_name="Site search"))
STATUS = descriptor("definition", label="Plan Status", frame=FRAME)
RECORD = descriptor("heading", "Member Record", match=NameMatch.contains, frame=FRAME)
FOUND = ElementPresent(kind="element_present", target=RECORD)
NOT_FOUND = TextPresent(kind="text_present", text="No member found", scope=None, frame_path=[])
BLANK = Observation(location="blank", nodes=[])


def contract() -> Contract:
    return Contract(
        name="lookup_plan_status",
        description="Plan status for a member.",
        inputs=[
            DeclaredInput(
                name="member_id",
                value_type=ValueType.string,
                required=True,
                sensitivity=Sensitivity.pii,
                description="Member id.",
                value="<param:member_id>",
            )
        ],
        outputs=[OutputSpec(name="plan_status", value_type=ValueType.string, description="Status.")],
    )


def evidenced(target: TargetDescriptor | None, snapshot: str) -> TargetDescriptor | None:
    if target is None:
        return None
    return target.model_copy(update={"evidence": EvidenceRef(ax_snapshot=snapshot, screenshot=None)})


def act(
    index: int,
    location: str,
    action: Action,
    target: TargetDescriptor | None,
    postcondition: Predicate | None,
    *,
    ok: bool = True,
    risk: RiskClass = RiskClass.read_only,
) -> TurnRecord:
    snapshot = f"ax/{index:03d}.json"
    if isinstance(postcondition, ElementPresent):
        postcondition = postcondition.model_copy(
            update={"target": evidenced(postcondition.target, f"ax/{index:03d}-after.json")}
        )
    return TurnRecord(
        event="turn",
        index=index,
        location=location,
        ax_snapshot=snapshot,
        response=Act(
            kind="act",
            intent=f"turn {index}",
            action=action,
            node_id=None if isinstance(action, Navigate) else f"n{index}",
            risk=risk,
            expect=ExpectLocation(kind="location", pattern="*"),
        ),
        target=evidenced(target, snapshot),
        postcondition=postcondition,
        ok=ok,
        expected=None if ok else "something",
        observed=None if ok else "something else",
        elapsed_ms=10,
    )


def finish(
    index: int, location: str, name: str, kind: OutcomeKind, detector: Predicate | None, binds: list[str]
) -> TurnRecord:
    return TurnRecord(
        event="turn",
        index=index,
        location=location,
        ax_snapshot=f"ax/{index:03d}.json",
        response=Finish(
            kind="finish",
            outcome_name=name,
            outcome_kind=kind,
            description=f"{name} description",
            detector=ExpectLocation(kind="location", pattern="*"),
            binds=binds,
        ),
        target=None,
        postcondition=detector,
        ok=True,
        expected=None,
        observed=None,
        elapsed_ms=10,
    )


def make_run(
    tmp_path: Path,
    name: str,
    turns: list[TurnRecord],
    pages: list[Observation],
    status: DiscoveryStatus,
) -> DiscoveryRun:
    """`pages[i]` is the observation turn i was taken on."""
    run_dir = tmp_path / name
    (run_dir / "ax").mkdir(parents=True)
    for turn, page in zip(turns, pages, strict=True):
        (run_dir / turn.ax_snapshot).write_text(page.model_dump_json())
    last = turns[-1].response
    return DiscoveryRun(
        path=run_dir / "transcript.jsonl",
        started=RunStarted(
            event="run_started",
            run_id=name,
            goal="Look up <param:member_id>",
            target="http://127.0.0.1:5000",
            app_id="memberserve",
            started_at="2026-09-15T16:05:05-0400",
        ),
        contract=contract(),
        turns=turns,
        finished=RunFinished(
            event="run_finished",
            status=status,
            outcome_name=last.outcome_name if isinstance(last, Finish) else None,
            outcome_kind=last.outcome_kind if isinstance(last, Finish) else None,
            outputs={},
            failure=None,
        ),
    )


def success_run(tmp_path: Path, *, navigate_to: str = "/members/search") -> DiscoveryRun:
    turns = [
        act(0, "blank", Navigate(kind="navigate", location=navigate_to), None,
            LocationMatches(kind="location_matches", pattern="/members/search")),
        # Dead end: the site-wide Search button, which did not submit the form.
        act(1, "/members/search", Click(kind="click"), SITE_SEARCH, None, ok=False),
        act(2, "/members/search", TypeText(kind="type_text", value=MEMBER_ID), BOX,
            ValueEquals(kind="value_equals", target=BOX, expected=MEMBER_ID), risk=RiskClass.reversible),
        act(3, "/members/search", Click(kind="click"), SEARCH,
            LocationMatches(kind="location_matches", pattern="/member/*")),
        act(4, "/member/<param:member_id>", ReadText(kind="read_text", bind_to="plan_status"), STATUS,
            ElementPresent(kind="element_present", target=STATUS)),
        finish(5, "/member/<param:member_id>", "found", OutcomeKind.success, FOUND, ["plan_status"]),
    ]
    pages = [BLANK, search_page(), search_page(), search_page(), record_frame(), record_frame()]
    return make_run(tmp_path, "success", turns, pages, DiscoveryStatus.success)


def not_found_run(
    tmp_path: Path, *, detector: Predicate | None = NOT_FOUND, second_target: TargetDescriptor = BOX
) -> DiscoveryRun:
    turns = [
        act(0, "blank", Navigate(kind="navigate", location="/members/search"), None,
            LocationMatches(kind="location_matches", pattern="/members/search")),
        act(1, "/members/search", TypeText(kind="type_text", value=MEMBER_ID), second_target,
            ValueEquals(kind="value_equals", target=second_target, expected=MEMBER_ID), risk=RiskClass.reversible),
        act(2, "/members/search", Click(kind="click"), SEARCH,
            LocationMatches(kind="location_matches", pattern="/member/*")),
        finish(3, "/member/<param:member_id>", "no_such_member", OutcomeKind.business, detector, []),
    ]
    pages = [BLANK, search_page(), search_page(), not_found_page()]
    return make_run(tmp_path, "not_found", turns, pages, DiscoveryStatus.business_outcome)


def test_dead_ends_pruned_and_parameters_by_name(tmp_path: Path) -> None:
    capability = compile(success_run(tmp_path), [], profile())

    assert [s.step_id for s in capability.steps] == [
        "navigate_members_search", "enter_member_id", "click_search", "read_plan_status",
    ]
    assert all(s.postcondition_timeout_ms == EXPECT_TIMEOUT_MS for s in capability.steps)
    assert capability.steps[1].action == TypeText(kind="type_text", value=MEMBER_ID)
    assert capability.steps[1].risk is RiskClass.reversible
    assert "<param:" not in capability.model_dump_json()
    assert capability.steps[1].target is not None
    assert capability.steps[1].target.evidence == EvidenceRef(ax_snapshot="ax/002.json", screenshot=None)

    assert [i.name for i in capability.inputs] == ["member_id"]
    assert not hasattr(capability.inputs[0], "value")
    assert [o.name for o in capability.outcomes] == ["found"]
    assert capability.checkpoint == capability.outcomes[0].detector
    assert capability.capability_id == "memberserve.lookup_plan_status"
    assert capability.target.app_version == "3.1"
    assert capability.target.location_pattern == "/member/*"
    assert capability.provenance is not None
    assert capability.provenance.transcript_run_id == "success"
    assert capability.provenance.compiler_version == COMPILER_VERSION


def test_business_run_contributes_outcome(tmp_path: Path) -> None:
    capability = compile(success_run(tmp_path), [not_found_run(tmp_path)], profile())

    assert [(o.name, o.kind) for o in capability.outcomes] == [
        ("no_such_member", OutcomeKind.business),
        ("found", OutcomeKind.success),
    ]
    assert capability.outcomes[0].detector == NOT_FOUND
    assert capability.outcomes[0].binds == []
    assert len(capability.steps) == 4


def test_business_run_must_be_prefix(tmp_path: Path) -> None:
    diverging = not_found_run(tmp_path, second_target=descriptor("textbox", "Site search terms"))
    with pytest.raises(Incompatible) as exc:
        compile(success_run(tmp_path), [diverging], profile())
    assert exc.value.turn == 1
    assert "enter_member_id" in exc.value.expected


def test_business_detector_must_not_hold_on_success_pages(tmp_path: Path) -> None:
    # "Member" is on the not-found page and on every page the success run walked.
    vague = TextPresent(kind="text_present", text="Member", scope=None, frame_path=[])
    with pytest.raises(Indiscriminate) as exc:
        compile(success_run(tmp_path), [not_found_run(tmp_path, detector=vague)], profile())
    assert exc.value.observed.endswith("success/ax/001.json")


def test_value_equals_is_not_an_outcome_detector(tmp_path: Path) -> None:
    detector = ValueEquals(kind="value_equals", target=BOX, expected=MEMBER_ID)
    with pytest.raises(NotCompilable) as exc:
        compile(success_run(tmp_path), [not_found_run(tmp_path, detector=detector)], profile())
    assert "value_equals" in exc.value.observed


def test_inlined_value_is_refused(tmp_path: Path) -> None:
    with pytest.raises(InlinedValue) as exc:
        compile(success_run(tmp_path, navigate_to="/member/<param:member_id>"), [], profile())
    assert exc.value.turn == 0


def test_run_status_is_checked(tmp_path: Path) -> None:
    primary = success_run(tmp_path)
    failed = DiscoveryRun(
        path=primary.path,
        started=primary.started,
        contract=primary.contract,
        turns=primary.turns,
        finished=primary.finished.model_copy(update={"status": DiscoveryStatus.failed}),
    )
    with pytest.raises(NotCompilable) as exc:
        compile(failed, [], profile())
    assert exc.value.observed == "failed"

    with pytest.raises(NotCompilable):
        compile(primary, [primary], profile())


def test_compile_is_deterministic_and_round_trips(tmp_path: Path) -> None:
    once = compile(success_run(tmp_path / "a"), [not_found_run(tmp_path / "a")], profile())
    twice = compile(success_run(tmp_path / "b"), [not_found_run(tmp_path / "b")], profile())
    assert once.model_dump_json() == twice.model_dump_json()
    assert Capability.model_validate_json(once.model_dump_json()) == once
    assert Capability.model_validate(json.loads(once.model_dump_json())) == once


def test_committed_transcripts_compile() -> None:
    capability = compile(load(SUCCESS_RUN), [load(NOT_FOUND_RUN)], profile())

    assert capability.capability_id == "memberserve.lookup_member_plan_status_and_renewal_date"
    assert [s.step_id for s in capability.steps] == [
        "navigate_members_search",
        "enter_member_id",
        "click_search",
        "read_plan_status",
        "read_renewal_date",
    ]
    assert [o.name for o in capability.outcomes] == [
        "member_not_found",
        "plan_status_and_renewal_date_found",
    ]
    assert capability.outcomes[1].binds == ["plan_status", "renewal_date"]
    assert capability.provenance is not None
    assert capability.provenance.transcript_run_id == "20260915T160505-f09d3b"
