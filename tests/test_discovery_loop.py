"""The discovery loop against a scripted surface and a scripted model: no API key,
no browser. Covers a dead end the model recovers from, a risky turn halting the
run before dispatch, redaction of every egress path, and fixture mode replaying
the transcript the loop itself wrote."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cua.discovery import (
    Act,
    Contract,
    DeclaredInput,
    DiscoveryFailureKind,
    DiscoveryStatus,
    ExpectElement,
    ExpectLocation,
    ExpectText,
    ExpectValue,
    Finish,
    GiveUp,
    Prompt,
    RunFinished,
    TranscriptDiverged,
    TranscriptModel,
    Turn,
    TurnRecord,
    discover,
    read,
)
from cua.schema import (
    Click,
    Navigate,
    OutcomeKind,
    OutputSpec,
    ParameterValue,
    ReadText,
    RiskClass,
    Sensitivity,
    TypeText,
    ValueType,
)
from cua.surface import Navigate as SurfaceNavigate

from .fixtures import ScriptedSurface, profile

GOAL = "Look up the plan status and renewal date for member 10001"
MEMBER_ID = "10001"


def contract() -> Contract:
    return Contract(
        name="lookup_member_status",
        description="Return a member's plan status and renewal date.",
        inputs=[
            DeclaredInput(
                name="member_id",
                value_type=ValueType.string,
                required=True,
                sensitivity=Sensitivity.pii,
                description="MemberServe member identifier.",
                value=MEMBER_ID,
            )
        ],
        outputs=[
            OutputSpec(name="plan_status", value_type=ValueType.string, description="Plan status."),
            OutputSpec(name="renewal_date", value_type=ValueType.date, description="Renewal date."),
        ],
    )


def act(action, node_id, expect, risk=RiskClass.read_only) -> Act:
    return Act(kind="act", intent="", action=action, node_id=node_id, risk=risk, expect=expect)


FRAME = ["Member Record"]
REF = ParameterValue(kind="parameter", name="member_id")


def read_step(bind_to: str, node_id: str) -> Act:
    return act(ReadText(kind="read_text", bind_to=bind_to), node_id,
               ExpectLocation(kind="location", pattern="/member/*"))


def happy_path(*, dead_end: bool = True, risky: bool = False) -> list[Turn]:
    turns: list[Turn] = [
        act(Navigate(kind="navigate", location="/members/search"), None,
            ExpectLocation(kind="location", pattern="/members/search")),
        act(TypeText(kind="type_text", value=REF), "box",
            ExpectValue(kind="value", node_id="box", expected=REF)),
        act(Click(kind="click"), "btn", ExpectLocation(kind="location", pattern="/member/*"),
            risk=RiskClass.risky if risky else RiskClass.read_only),
    ]
    if dead_end:
        # Expects "a definition" after reading: both definitions are unnamed, so
        # the expectation is ambiguous and the turn is a dead end.
        turns.append(act(ReadText(kind="read_text", bind_to="plan_status"), "d1",
                         ExpectElement(kind="element", role="definition", accessible_name="",
                                       name_match="exact", label=None, frame_path=FRAME)))
    turns += [read_step("plan_status", "d1"), read_step("renewal_date", "d2"), finish_found()]
    return turns


class ScriptedModel:
    def __init__(self, turns: list[Turn]) -> None:
        self.turns = list(turns)
        self.prompts: list[Prompt] = []

    def declare(self, goal: str) -> Contract:
        self.declared_goal = goal
        return contract()

    def next_turn(self, prompt: Prompt) -> Turn:
        self.prompts.append(prompt)
        return self.turns.pop(0)


def finish_found() -> Finish:
    return Finish(
        kind="finish", outcome_name="found", outcome_kind=OutcomeKind.success, description="Record loaded.",
        detector=ExpectElement(kind="element", role="heading", accessible_name="Member Record",
                               name_match="contains", label=None, frame_path=FRAME),
        binds=["plan_status", "renewal_date"],
    )


def run(tmp_path: Path, turns: list[Turn], surface: ScriptedSurface | None = None):
    surface = surface or ScriptedSurface()
    model = ScriptedModel(turns)
    result = discover(GOAL, surface, profile(), model, tmp_path, target="http://test")
    return result, surface, model


def test_success_with_dead_end_recorded(tmp_path: Path) -> None:
    result, surface, model = run(tmp_path, happy_path())

    assert result.status is DiscoveryStatus.success
    assert result.outcome_name == "found"
    assert result.outputs == {"plan_status": "Active", "renewal_date": "2027-01-15"}
    assert result.dead_ends == 1
    assert result.turns == 7

    entries = read(Path(result.transcript_path))
    records = [e for e in entries if isinstance(e, TurnRecord)]
    assert [r.ok for r in records] == [True, True, True, False, True, True, True]
    assert records[3].expected and records[3].observed
    # The dead end was reported back to the model on its next turn.
    assert model.prompts[4].history[-1].result.startswith("error")
    # Every action was dispatched through the session (the surface saw them all).
    assert len(surface.acted) == 6
    # Derived descriptors and postconditions are on the successful turns.
    submit = records[2]
    assert submit.target is not None and submit.target.container is not None
    assert submit.target.container.accessible_name == "Member search"
    assert submit.postcondition is not None and submit.postcondition.kind == "location_matches"
    finished = entries[-1]
    assert isinstance(finished, RunFinished) and finished.outcome_kind is OutcomeKind.success


def test_risky_turn_halts_before_dispatch(tmp_path: Path) -> None:
    result, surface, _ = run(tmp_path, happy_path(dead_end=False, risky=True))
    assert result.status is DiscoveryStatus.failed
    assert result.failure is not None
    assert result.failure.kind is DiscoveryFailureKind.approval_required
    assert result.failure.turn == 2
    # navigate + type, then the risky click never reached the surface
    assert [type(a) for a, _ in surface.acted][-1].__name__ == "TypeText"
    assert len(surface.acted) == 2


def test_give_up_and_budget(tmp_path: Path) -> None:
    result, _, _ = run(tmp_path, [GiveUp(kind="give_up", reason="stuck")])
    assert result.status is DiscoveryStatus.failed
    assert result.failure is not None and result.failure.kind is DiscoveryFailureKind.gave_up


def test_nothing_sensitive_leaves(tmp_path: Path) -> None:
    turns = happy_path(dead_end=False)
    result, surface, model = run(tmp_path, turns)

    # The declare call sees the raw goal, exactly once.
    assert MEMBER_ID in model.declared_goal
    # No later prompt does, even though the typed value is on the page after turn 1.
    for prompt in model.prompts:
        assert MEMBER_ID not in prompt.system
        assert MEMBER_ID not in prompt.observation
        for exchange in prompt.history:
            assert MEMBER_ID not in exchange.response and MEMBER_ID not in exchange.result
    assert "<param:member_id>" in model.prompts[2].observation
    # Nothing on disk carries it either, and the prompts are on disk.
    run_dir = Path(result.transcript_path).parent
    prompts = sorted((run_dir / "prompts").iterdir())
    assert len(prompts) == result.turns
    assert "Current observation:" in prompts[0].read_text()
    for path in run_dir.rglob("*"):
        if path.is_file():
            assert MEMBER_ID not in path.read_text(), path
    # But the surface got the real value.
    typed = [a for a, _ in surface.acted if type(a).__name__ == "TypeText"]
    assert typed[0].text == MEMBER_ID
    # And the result's contract is redacted while its outputs are not.
    assert result.contract.inputs[0].value == "<param:member_id>"
    assert result.outputs["plan_status"] == "Active"


def test_fixture_mode_replays_the_transcript(tmp_path: Path) -> None:
    first, _, _ = run(tmp_path, happy_path())
    entries = read(Path(first.transcript_path))

    with pytest.raises(Exception, match="redacted in the transcript"):
        TranscriptModel(entries, {})

    model = TranscriptModel(entries, {"member_id": MEMBER_ID})
    surface = ScriptedSurface()
    second = discover(GOAL, surface, profile(), model, tmp_path / "again", target="http://test")
    assert second.status is DiscoveryStatus.success
    assert second.outputs == first.outputs
    assert second.dead_ends == first.dead_ends
    assert second.run_id != first.run_id


def test_fixture_mode_detects_divergence(tmp_path: Path) -> None:
    turns = happy_path(dead_end=False)
    first, _, _ = run(tmp_path, turns)
    entries = read(Path(first.transcript_path))
    # A surface that starts somewhere else than the recording did.
    surface = ScriptedSurface()
    surface.acted.append((SurfaceNavigate("/members/search"), None))
    model = TranscriptModel(entries, {"member_id": MEMBER_ID})
    result = discover(GOAL, surface, profile(), model, tmp_path / "again", target="http://test")
    assert result.status is DiscoveryStatus.failed
    assert result.failure is not None
    assert result.failure.kind is DiscoveryFailureKind.model_error
    assert "recorded at '/'" in result.failure.observed


class RenamedSurface(ScriptedSurface):
    """Same pages, different opaque node ids: what a second browser session looks like."""

    def observe(self):
        observation = super().observe()
        nodes = [
            n.model_copy(update={"node_id": f"x-{n.node_id}",
                                 "parent_id": f"x-{n.parent_id}" if n.parent_id else None})
            for n in observation.nodes
        ]
        return observation.model_copy(update={"nodes": nodes})

    def act(self, action, node):
        if node is not None:
            node = node.model_copy(update={"node_id": node.node_id.removeprefix("x-")})
        return super().act(action, node)


def test_fixture_mode_rebinds_node_ids_through_descriptors(tmp_path: Path) -> None:
    first, _, _ = run(tmp_path, happy_path())
    entries = read(Path(first.transcript_path))
    model = TranscriptModel(entries, {"member_id": MEMBER_ID})
    surface = RenamedSurface()
    second = discover(GOAL, surface, profile(), model, tmp_path / "again", target="http://test")
    assert second.status is DiscoveryStatus.success, second.failure
    assert second.outputs == first.outputs
    assert second.dead_ends == first.dead_ends
    acted_on = [node_id for _, node_id in surface.acted if node_id]
    assert acted_on == ["box", "btn", "d1", "d1", "d2"]


def test_expectation_quoting_a_read_value_is_a_dead_end(tmp_path: Path) -> None:
    turns = happy_path(dead_end=False)
    # After reading plan_status, expect its literal value on the page: true now,
    # false for any other member, so the loop refuses it.
    turns.insert(4, act(ReadText(kind="read_text", bind_to="plan_status"), "d1",
                        ExpectText(kind="text", text="Active", frame_path=FRAME)))
    result, _, model = run(tmp_path, turns)
    assert result.status is DiscoveryStatus.success
    assert result.dead_ends == 1
    assert "quotes the value of 'plan_status'" in model.prompts[5].history[-1].result
    # Quoting the redacted placeholder can never hold either.
    turns = happy_path(dead_end=False)
    turns.insert(-1, Finish(kind="finish", outcome_name="found", outcome_kind=OutcomeKind.success,
                            description="", binds=["plan_status", "renewal_date"],
                            detector=ExpectText(kind="text", text="Member <param:member_id>", frame_path=[])))
    result, _, model = run(tmp_path, turns)
    assert result.status is DiscoveryStatus.success and result.dead_ends == 1
    assert "redacted input placeholder" in model.prompts[-1].history[-1].result
    # The finish detector is held to the same rule.
    turns = happy_path(dead_end=False)
    turns.insert(-1, Finish(kind="finish", outcome_name="found", outcome_kind=OutcomeKind.success,
                            description="", detector=ExpectText(kind="text", text="2027-01-15", frame_path=FRAME),
                            binds=["plan_status", "renewal_date"]))
    result, _, _ = run(tmp_path, turns)
    assert result.status is DiscoveryStatus.success and result.dead_ends == 1


def test_repeating_a_failed_turn_is_refused_then_halts(tmp_path: Path) -> None:
    bad = act(Click(kind="click"), "nope", ExpectLocation(kind="location", pattern="/member/*"))
    turns = happy_path(dead_end=False)[:1] + [bad, bad, bad, bad, bad]
    result, surface, model = run(tmp_path, turns)
    assert result.status is DiscoveryStatus.failed
    assert result.failure is not None and result.failure.kind is DiscoveryFailureKind.stuck
    # navigate, one real attempt (unknown node), two refused repeats, then the halt
    assert result.turns == 5 and result.dead_ends == 3
    assert len(surface.acted) == 1
    assert "not executed" in model.prompts[3].history[-1].result


def test_failed_element_expectation_lists_candidates(tmp_path: Path) -> None:
    turns = happy_path(dead_end=False)
    turns.insert(-1, Finish(kind="finish", outcome_name="found", outcome_kind=OutcomeKind.success,
                            description="", binds=["plan_status", "renewal_date"],
                            detector=ExpectElement(kind="element", role="definition",
                                                   accessible_name="Plan Status", name_match="exact",
                                                   label=None, frame_path=FRAME)))
    result, _, model = run(tmp_path, turns)
    assert result.status is DiscoveryStatus.success and result.dead_ends == 1
    feedback = model.prompts[-1].history[-1].result
    assert "2 'definition' node(s)" in feedback and "label='Plan Status'" in feedback
