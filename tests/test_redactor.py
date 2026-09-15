"""Known PII and secret shapes, and the runtime values of sensitive inputs, are
stripped from every egress path: failure strings on the result and every event
line. The log never sees a typed value at all."""

from __future__ import annotations

import io
import json

import pytest

from cua.policy import Policy, PolicyDenied, Redactor
from cua.replay import EventLog, Session, replay
from cua.schema import FailureKind, ParameterSpec, RiskClass, Sensitivity, ValueType
from cua.surface import Navigate, TypeText

from .fixtures import ScriptedSurface, capability, node, profile, record_frame


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("mail me at jo.bloggs+x@example.co.uk now", "mail me at <redacted:email> now"),
        ("ssn 123-45-6789.", "ssn <redacted:ssn>."),
        ("call (555) 123-4567 or 555-123-4567", "call <redacted:phone> or <redacted:phone>"),
        ("Authorization: Bearer abc.DEF-123_456", "Authorization: <redacted:token>"),
        ("api_key=sk_live_0123456789", "<redacted:token>"),
        ("key sk-abcdefghijklmnopqrstuvwxyz", "key <redacted:token>"),
        ("location '/member/10001', 12 nodes", "location '/member/10001', 12 nodes"),
    ],
)
def test_fixed_patterns(text: str, expected: str) -> None:
    assert Redactor(()).redact(text) == expected


def test_parameter_values_are_replaced_on_token_boundaries() -> None:
    redactor = Redactor((("member_id", "1"),))
    assert redactor.redact("member 1 at '/member/1' vs 10001") == (
        "member <param:member_id> at '/member/<param:member_id>' vs 10001"
    )


def test_only_sensitive_inputs_are_collected() -> None:
    cap = capability()
    extra = ParameterSpec(
        name="plan", value_type=ValueType.string, required=False,
        sensitivity=Sensitivity.none, description="",
    )
    secret = ParameterSpec(
        name="pin", value_type=ValueType.string, required=False,
        sensitivity=Sensitivity.secret, description="",
    )
    cap = cap.model_copy(update={"inputs": [*cap.inputs, extra, secret]})
    redactor = Redactor.for_run(cap, {"member_id": "10001", "plan": "Active", "pin": "10001-x"})
    assert redactor.values == (("pin", "10001-x"), ("member_id", "10001"))
    assert redactor.redact("Active 10001-x 10001") == "Active <param:pin> <param:member_id>"


def test_failure_strings_and_event_lines_are_redacted() -> None:
    # A record page whose heading has drifted: the checkpoint fails, and the observed
    # summary carries the location, which contains the member id.
    page = record_frame()
    nodes = [n.model_copy(update={"name": "Record"}) if n.node_id == "h" else n for n in page.nodes]
    nodes = [n.model_copy(update={"name": "Record"}) if n.node_id == "fh" else n for n in nodes]
    stream = io.StringIO()
    result = replay(
        capability(),
        {"member_id": "10001"},
        ScriptedSurface(page.model_copy(update={"nodes": nodes})),
        profile(),
        log_stream=stream,
    )

    assert result.failure is not None and result.failure.kind is FailureKind.checkpoint_failed
    assert "<param:member_id>" in result.failure.observed
    assert "10001" not in result.model_dump_json()

    lines = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert "10001" not in stream.getvalue()
    assert all({"ts", "event"} <= set(line) for line in lines)
    assert [line["event"] for line in lines] == [
        "action", "step_completed",
        "action", "step_completed",
        "action", "step_completed",
        "action", "step_completed",
        "action", "step_completed",
        "run_finished",
    ]
    assert lines[-1] == {**lines[-1], "status": "failed", "failure_kind": "checkpoint_failed"}


def test_log_never_sees_typed_text_even_when_not_sensitive() -> None:
    stream = io.StringIO()
    surface = ScriptedSurface()
    session = Session(surface, {}, Policy(("*",), True), EventLog(stream, Redactor(())))
    session.observe()
    session.act(TypeText("plainly-visible"), node("box", "textbox", "Member ID"), RiskClass.read_only)

    assert surface.acted[0][0] == TypeText("plainly-visible")
    line = json.loads(stream.getvalue())
    assert "plainly-visible" not in stream.getvalue()
    assert line["event"] == "action" and line["kind"] == "typetext"
    assert line["target"] == {"role": "textbox", "name": "Member ID"}


def test_policy_denial_is_logged_then_raised() -> None:
    stream = io.StringIO()
    surface = ScriptedSurface()
    session = Session(surface, {}, Policy(("/x",), False), EventLog(stream, Redactor(())))
    with pytest.raises(PolicyDenied, match="/y"):
        session.act(Navigate("/y"), None, RiskClass.read_only)

    assert surface.acted == []
    events = [json.loads(line)["event"] for line in stream.getvalue().splitlines()]
    assert events == ["policy_denied"]


def test_silent_log_writes_nothing() -> None:
    EventLog(None, Redactor(())).emit("action", step_id="s")  # must not raise
