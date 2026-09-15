"""The discovery loop: an LLM drives a live surface, one structured turn per
observation, until it declares an outcome, gives up, or runs out of turns.

Everything the model decides is checked before it is trusted (`execute.py`). A
turn that fails a check is recorded as a dead end and reported back to the model
as its next result -- the run continues, the transcript keeps the dead end, and
item 8 prunes it.

Every action goes through `Session.act()` with `approve_risky=False`, so a risky
turn halts the run (invariant 12). An interstitial halts it too: discovery does
not dismiss notices, so "click Continue" can never be recorded as a step."""

from __future__ import annotations

import secrets
import time
from pathlib import Path
from typing import TextIO

from cua import schema
from cua.policy import Policy, Redactor
from cua.replay import EventLog, InterstitialDetected, Session, screened
from cua.surface import Surface, SurfaceError, resolve

from .execute import MAX_REPEATS, DeadEnd, Halt, RunState, act, finish, repeated, snapshot_to
from .model import Exchange, Model, ModelError, Prompt
from .prompt import turn_system
from .render import render
from .transcript import (
    ContractDeclared,
    DiscoveryFailure,
    DiscoveryFailureKind,
    DiscoveryStatus,
    RunFinished,
    RunStarted,
    TranscriptWriter,
    TurnRecord,
)
from .turns import Act, Contract, Finish, GiveUp

MAX_TURNS = 20


class DiscoveryResult(schema.StrictModel):
    """Input values on `contract` are redacted the way the transcript is; outputs
    are the answer and are not."""

    run_id: str
    status: DiscoveryStatus
    contract: Contract
    outcome_name: str | None
    outcome_kind: schema.OutcomeKind | None
    outputs: dict[str, str]
    turns: int
    dead_ends: int
    failure: DiscoveryFailure | None
    transcript_path: str


def discover(
    goal: str,
    surface: Surface,
    profile: schema.AppProfile,
    model: Model,
    evidence_dir: Path,
    *,
    target: str,
    log_stream: TextIO | None = None,
) -> DiscoveryResult:
    contract = model.declare(goal)
    values = {i.name: i.value for i in contract.inputs}
    redactor = Redactor.for_inputs(contract.inputs, values)

    run_id = time.strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(3)
    run_dir = evidence_dir / run_id
    (run_dir / "ax").mkdir(parents=True)
    (run_dir / "prompts").mkdir()
    transcript_path = run_dir / "transcript.jsonl"

    with transcript_path.open("w") as stream:
        writer = TranscriptWriter(stream, redactor)
        writer.write(
            RunStarted(
                event="run_started",
                run_id=run_id,
                goal=goal,
                target=target,
                app_id=profile.app_id,
                started_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            )
        )
        writer.write(ContractDeclared(event="contract_declared", contract=contract))
        policy = Policy(tuple(profile.allowed_locations), approve_risky=False)
        session = Session(surface, values, policy, EventLog(log_stream, redactor))
        run = RunState(contract, profile, model, session, redactor, writer, run_dir)
        finished = _execute(run)
        writer.write(finished)

    return DiscoveryResult(
        run_id=run_id,
        status=finished.status,
        contract=_redacted_contract(contract, redactor),
        outcome_name=finished.outcome_name,
        outcome_kind=finished.outcome_kind,
        outputs=finished.outputs,
        turns=len(run.history),
        dead_ends=run.dead_ends,
        failure=finished.failure,
        transcript_path=str(transcript_path),
    )


def _execute(run: RunState) -> RunFinished:
    system = run.redactor.redact(turn_system(run.contract, run.profile))
    for index in range(MAX_TURNS):
        run.session.step_id = f"turn_{index}"
        started = time.monotonic()
        try:
            observation = screened(run.session.observe(), run.profile, run.session)
        except InterstitialDetected as exc:
            return _failed(run, DiscoveryFailureKind.interstitial, index, exc.expected, exc.observed)
        except SurfaceError as exc:
            return _failed(run, DiscoveryFailureKind.surface_error, index, "an observation", str(exc))
        snapshot = snapshot_to(run, f"ax/{index:03d}.json", observation)

        prompt = Prompt(
            system=system,
            history=tuple(run.history),
            location=run.redactor.redact(observation.location),
            observation=run.redactor.redact(render(observation)),
            locate=lambda descriptor: resolve(observation, descriptor).node.node_id,
        )
        _save_prompt(run, index, prompt)
        try:
            turn = run.model.next_turn(prompt)
        except ModelError as exc:
            return _failed(run, DiscoveryFailureKind.model_error, index, "a structured turn", str(exc))

        target: schema.TargetDescriptor | None = None
        postcondition: schema.Predicate | None = None
        problem: DeadEnd | Halt | None = None
        try:
            repeats = repeated(turn, run)
            if repeats >= MAX_REPEATS:
                raise Halt(
                    DiscoveryFailureKind.stuck,
                    "a different action or expectation",
                    f"the same failed turn {repeats + 1} times in a row",
                )
            if repeats:
                raise DeadEnd(
                    "a different action or expectation after an error",
                    f"identical to turn {index - 1}, which failed; not executed",
                )
            if isinstance(turn, Act):
                target, postcondition = act(run, turn, observation, snapshot, index)
            elif isinstance(turn, Finish):
                postcondition = finish(run, turn, observation, snapshot)
        except DeadEnd as exc:
            problem = exc
            target = exc.target
            run.dead_ends += 1
        except Halt as exc:
            problem = exc
            target = exc.target

        run.writer.write(
            TurnRecord(
                event="turn",
                index=index,
                location=observation.location,
                ax_snapshot=snapshot,
                response=turn,
                target=target,
                postcondition=postcondition,
                ok=problem is None,
                expected=problem.expected if problem else None,
                observed=problem.observed if problem else None,
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )
        )
        result = "ok" if problem is None else f"error: {problem}"
        run.history.append(
            Exchange(run.redactor.redact(turn.model_dump_json()), run.redactor.redact(result))
        )
        run.session.log.emit("turn", index=index, kind=turn.kind, ok=problem is None)

        if isinstance(problem, Halt):
            return _failed(run, problem.kind, index, problem.expected, problem.observed)
        if isinstance(turn, GiveUp):
            return _failed(run, DiscoveryFailureKind.gave_up, index, "progress", turn.reason)
        if isinstance(turn, Finish) and problem is None:
            return _finished(run, turn)

    return _failed(
        run,
        DiscoveryFailureKind.budget_exhausted,
        MAX_TURNS,
        f"an outcome within {MAX_TURNS} turns",
        "budget exhausted",
    )


def _save_prompt(run: RunState, index: int, prompt: Prompt) -> None:
    """The literal text the model was given this turn, already redacted, laid out
    as the adapter sends it: system, then each prior exchange, then the observation."""
    parts = [f"=== system ===\n{prompt.system}"]
    for exchange in prompt.history:
        parts.append(f"=== assistant ===\n{exchange.response}\n=== user ===\nResult: {exchange.result}")
    parts.append(f"=== user ===\nCurrent observation:\n{prompt.observation}")
    (run.run_dir / "prompts" / f"{index:03d}.txt").write_text("\n\n".join(parts) + "\n")


def _failed(
    run: RunState, kind: DiscoveryFailureKind, turn: int, expected: str, observed: str
) -> RunFinished:
    failure = DiscoveryFailure(
        kind=kind,
        turn=turn,
        expected=run.redactor.redact(expected),
        observed=run.redactor.redact(observed),
    )
    run.session.log.emit("run_finished", status="failed", failure_kind=kind.value, turn=turn)
    return RunFinished(
        event="run_finished",
        status=DiscoveryStatus.failed,
        outcome_name=None,
        outcome_kind=None,
        outputs={},
        failure=failure,
    )


def _finished(run: RunState, turn: Finish) -> RunFinished:
    status = (
        DiscoveryStatus.success
        if turn.outcome_kind is schema.OutcomeKind.success
        else DiscoveryStatus.business_outcome
    )
    run.session.log.emit("run_finished", status=status.value, outcome=turn.outcome_name)
    return RunFinished(
        event="run_finished",
        status=status,
        outcome_name=turn.outcome_name,
        outcome_kind=turn.outcome_kind,
        outputs={name: run.outputs[name] for name in turn.binds},
        failure=None,
    )


def _redacted_contract(contract: Contract, redactor: Redactor) -> Contract:
    inputs = [i.model_copy(update={"value": redactor.redact(i.value)}) for i in contract.inputs]
    return contract.model_copy(update={"inputs": inputs})
