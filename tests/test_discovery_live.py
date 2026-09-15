"""Discovery against the real app through the real adapter, headless, with a
rule-based model that picks nodes the way the LLM does -- from the rendered
observation. Proves the surface path (a blank start, the iframe, opaque node
ids) and that the transcript the loop writes is fixture-replayable in a fresh
browser session. No API key."""

from __future__ import annotations

import re
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from werkzeug.serving import make_server

from app.server import create_app
from cua.discovery import (
    Act,
    Contract,
    DeclaredInput,
    DiscoveryStatus,
    ExpectElement,
    ExpectLocation,
    ExpectValue,
    Finish,
    Prompt,
    TranscriptModel,
    Turn,
    discover,
    read,
)
from cua.schema import (
    Click,
    NameMatch,
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
from cua.surface import PlaywrightWebSurface

from .fixtures import profile

GOAL = "Look up the plan status and renewal date for member 10001"
LINE = re.compile(r"^\s*\[(?P<id>[^\]]+)\] (?P<role>\S+)(?P<rest>.*)$")
REF = ParameterValue(kind="parameter", name="member_id")
FRAME = ["Member Record"]


@pytest.fixture(scope="module")
def base_url() -> Iterator[str]:
    server = make_server("127.0.0.1", 0, create_app("a"), threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def find(observation: str, role: str, *, name: str | None = None, label: str | None = None, last: bool = False) -> str:
    hits = []
    for line in observation.splitlines():
        m = LINE.match(line)
        if m is None or m["role"] != role:
            continue
        if name is not None and f"name={name!r}" not in m["rest"]:
            continue
        if label is not None and f"label={label!r}" not in m["rest"]:
            continue
        hits.append(m["id"])
    assert hits, (role, name, label)
    return hits[-1] if last else hits[0]


def act(action, node_id, expect) -> Act:
    return Act(kind="act", intent="", action=action, node_id=node_id, risk=RiskClass.read_only, expect=expect)


class Oracle:
    """Types the id, clicks the *site-wide* Search first (a dead end that empties
    the form), then the form's own Search, reads both outputs, finishes."""

    def __init__(self) -> None:
        self.calls = 0

    def declare(self, goal: str) -> Contract:
        return Contract(
            name="lookup_member_status",
            description="Return a member's plan status and renewal date.",
            inputs=[DeclaredInput(name="member_id", value_type=ValueType.string, required=True,
                                  sensitivity=Sensitivity.pii, description="Member id.", value="10001")],
            outputs=[OutputSpec(name="plan_status", value_type=ValueType.string, description="Status."),
                     OutputSpec(name="renewal_date", value_type=ValueType.date, description="Renewal.")],
        )

    def next_turn(self, prompt: Prompt) -> Turn:
        o = prompt.observation
        self.calls += 1
        match self.calls:
            case 1:
                return act(Navigate(kind="navigate", location="/members/search"), None,
                           ExpectLocation(kind="location", pattern="/members/search"))
            case 2:
                # Dead end: an unknown node id is refused before anything is dispatched.
                return act(Click(kind="click"), "not-a-node", ExpectLocation(kind="location", pattern="/member/*"))
            case 3:
                box = find(o, "textbox", name="Member ID")
                return act(TypeText(kind="type_text", value=REF), box,
                           ExpectValue(kind="value", node_id=box, expected=REF))
            case 4:
                return act(Click(kind="click"), find(o, "button", name="Search", last=True),
                           ExpectLocation(kind="location", pattern="/member/*"))
            case 5:
                return act(ReadText(kind="read_text", bind_to="plan_status"), find(o, "definition", label="Plan Status"),
                           ExpectElement(kind="element", role="heading", accessible_name="Member Record",
                                         name_match=NameMatch.contains, label=None, frame_path=FRAME))
            case 6:
                return act(ReadText(kind="read_text", bind_to="renewal_date"), find(o, "definition", label="Renewal Date"),
                           ExpectLocation(kind="location", pattern="/member/*"))
        return Finish(kind="finish", outcome_name="found", outcome_kind=OutcomeKind.success, description="Loaded.",
                      detector=ExpectElement(kind="element", role="heading", accessible_name="Member Record",
                                             name_match=NameMatch.contains, label=None, frame_path=FRAME),
                      binds=["plan_status", "renewal_date"])


def test_discovery_then_fixture_replay_against_the_app(base_url: str, tmp_path: Path) -> None:
    with PlaywrightWebSurface(base_url, headless=True) as surface:
        first = discover(GOAL, surface, profile(), Oracle(), tmp_path, target=base_url)
    assert first.status is DiscoveryStatus.success, first.failure
    assert first.outputs == {"plan_status": "Active", "renewal_date": "2027-01-15"}
    assert first.dead_ends == 1

    run_dir = Path(first.transcript_path).parent
    for path in run_dir.rglob("*"):
        if path.is_file():
            assert "10001" not in path.read_text(), path

    entries = read(Path(first.transcript_path))
    model = TranscriptModel(entries, {"member_id": "10002"})
    with PlaywrightWebSurface(base_url, headless=True) as surface:
        second = discover(GOAL, surface, profile(), model, tmp_path / "again", target=base_url)
    assert second.status is DiscoveryStatus.success, second.failure
    assert second.outputs == {"plan_status": "Lapsed", "renewal_date": "2026-03-31"}
    assert second.dead_ends == 1
