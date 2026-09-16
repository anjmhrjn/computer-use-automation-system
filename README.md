# Computer-use automation: discover once, replay forever

An LLM works out how to accomplish a task in a legacy UI **once**. That run is
compiled into a typed, versioned **capability** artifact. The artifact is then
**replayed** deterministically in production — no model in the loop — with
outcome detection, error classification, human escalation and safety guardrails.

This file is operational: how to set up, run, and read what the system produces.
The reasoning behind each design choice is in [`docs/decisions.md`](docs/decisions.md),
one section per build item.

## Setup

Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run playwright install chromium
```

`OPENAI_API_KEY` is needed only for a live `cua discover`. Everything else —
fixture-mode discovery, compile, replay, the console, the test suite — runs without it.

## Run the pipeline

Each command below is one stage. Run them in this order from the repo root.

```bash
# 1. The target app. "MemberServe 3.1" is a self-built hostile legacy UI (Flask, one iframe,
#    fault injection). Seed members are in app/members.json; 20001 is permission-restricted.
uv run python -m app.server                        # :5000
uv run python -m app.server --variant b            # :5001, same app, tenant B's labels

# 2. Discovery: the model drives the live app to reach a goal. Writes evidence/<run_id>/.
uv run cua discover --goal "Look up the plan status and renewal date for member 10001"
uv run cua discover --goal "Look up the plan status and renewal date for member 99999"   # business outcome: no such member

#    Fixture mode replays a recorded transcript's model turns in a fresh browser — no API key.
#    Inputs the transcript redacted are supplied by name.
uv run cua discover --from-transcript evidence/20260915T160505-f09d3b/transcript.jsonl --param member_id=10002

# 3. Compile: the success transcript first, then any business-outcome transcripts of the same goal.
#    Rules only, no model. Writes artifacts/<app_id>.<contract_name>.json.
uv run cua compile evidence/20260915T160505-f09d3b/transcript.jsonl evidence/20260915T160854-f94d73/transcript.jsonl
uv run cua validate artifacts/memberserve.lookup_member_plan_status_and_renewal_date.json
uv run cua schema > schema/capability.schema.json  # regenerate the JSON Schema from the Pydantic models

# 4. Replay: zero LLM calls. Writes evidence/<run_id>/ and prints the structured result.
uv run cua replay artifacts/memberserve.lookup_member_plan_status_and_renewal_date.json --param member_id=10001   # success
uv run cua replay artifacts/memberserve.lookup_member_plan_status_and_renewal_date.json --param member_id=99999   # business outcome
uv run cua replay artifacts/memberserve.lookup_member_plan_status_and_renewal_date.json --param member_id=10001 --target http://127.0.0.1:5001   # hard failure: target_drift

#    Tenant overlay: the same artifact against variant-b, with tenant B's renames applied
#    from artifacts/tenants/b.json before the run. The caller names the tenant; nothing is inferred.
uv run cua replay artifacts/memberserve.lookup_member_plan_status_and_renewal_date.json --param member_id=10001 --target http://127.0.0.1:5001 --tenant b

# 5. Escalation: hand an environment-blocked failure to a human. Start the mock operator console,
#    then replay with --console; the run holds control open until the operator resolves it.
uv run cua serve                                   # :8000
uv run cua replay artifacts/memberserve.lookup_member_plan_status_and_renewal_date.json --param member_id=10001 --console http://127.0.0.1:8000
```

`--approve-risky` on `replay` lets risky-class steps execute; without it they halt
(or escalate, with `--console`). The seeded capability has none.

### Triggering failures

Faults are armed out-of-band through the app's `/__faults` endpoint — never by the
automation — so nothing fault-related can leak into an artifact.

```bash
curl -X POST http://127.0.0.1:5000/__faults -H 'content-type: application/json' \
     -d '{"kind": "session_expired", "mode": "once"}'
curl -X DELETE http://127.0.0.1:5000/__faults        # clear everything
```

| `kind` | What the next ordinary request gets | Replay classifies it as |
|---|---|---|
| `session_expired` | undismissable "session expired" interstitial | `session_expired` — hard failure, escalates |
| `maintenance_notice` | dismissable notice; `mode: until_cleared` makes it come back | dismissed and the step re-run; `interstitial_persisted` after the dismissal ceiling |
| `timeout` | request hangs until the fault is cleared | `timeout` on the step budget, escalates |

`mode` is `once` (spent by the first hit) or `until_cleared`. Two more failure
classes need no fault: member `20001` yields `permission_denied` (an access-denied
page), and replaying against variant-b yields `target_drift` (the resolver cannot pin
one node).

## Repo layout

```
src/cua/
  schema/       Pydantic artifact types: Capability, Step, TargetDescriptor, ReplayResult, AppProfile, InterventionRequest
  surface/      Surface port, PlaywrightWebSurface (AX tree via CDP), tiered resolver. Nothing web-specific crosses the port.
  replay/       Engine, wait/predicates, interstitial screening, error classification, evidence writer
  discovery/    Agent loop, Model port, OpenAI adapter, strict-schema transform, transcript + fixture mode
  compiler/     Transcript -> Capability. Deterministic, no model.
  policy/       Location allowlist, risk classes, redactor
  escalation/   Control token, intervention request, before/after diff, resume
  console/      stdlib mock operator console (`cua serve`)
  cli.py        validate | schema | discover | compile | replay | serve
app/            MemberServe 3.1 + variant-b. Scaffolding, not a deliverable.
artifacts/      Capabilities and per-app profiles
evidence/       One directory per run
schema/         capability.schema.json, generated by `cua schema`
docs/           decisions.md
tests/
```

## Artifacts

| File | What it is |
|---|---|
| `artifacts/lookup_member_status.json` | Hand-written capability from item 1. The shape discovery had to learn to emit; still the fixture for the replay and adapter tests. |
| `artifacts/memberserve.lookup_member_plan_status_and_renewal_date.json` | Compiled from the two committed discovery runs. Carries both the success outcome and `member_not_found`. |
| `artifacts/apps/memberserve.json` | App profile: allowed locations and the interstitial catalogue (detector, dismiss action, failure kind). |
| `artifacts/tenants/b.json` | Tenant overlay: base→tenant renames of the strings descriptors key on (`renames`), and of frame titles separately (`frames`). Applied by `replay --tenant b`; one file serves every capability for the app. |
| `schema/capability.schema.json` | JSON Schema for the artifact, generated from the Pydantic models. |

A capability declares `schema_version`, `version`, `target.app_id` /
`target.app_version`, typed inputs and outputs, named outcomes (success and
business), ordered steps with a `TargetDescriptor` and a postcondition each, and a
final checkpoint. Parameters are referenced by name; no value is ever inlined.

## Reading an evidence directory

Everything under `evidence/` has been through the redactor. Three shapes exist.

**Discovery run** — `evidence/20260915T160505-f09d3b/` (success) and
`evidence/20260915T160854-f94d73/` (business outcome, `member_not_found`)

| Path | Contents |
|---|---|
| `transcript.jsonl` | Every model turn, including dead ends, and the final `run_finished` line. Input to `cua compile` and `--from-transcript`. |
| `events.jsonl` | Structured log of what the loop did with each turn. |
| `prompts/NNN.txt` | The exact prompt sent for turn N — proof that parameter values never reach the model after the goal is declared. |
| `ax/NNN.json`, `ax/NNN-after.json` | Observation before and after turn N's action. |

**Replay run** — `evidence/20260915T184240-6afcac/` (success),
`evidence/20260915T184249-f40405/` (business outcome),
`evidence/20260915T184255-01415b/` (hard failure, `target_drift` against variant-b),
`evidence/20260916T103358-43b02b/` (success against variant-b with `--tenant b`; `events.jsonl`
opens with an `overlay_applied` line naming which renames fired and which the artifact never
used), `evidence/20260916T103359-b6838d/` (the same run without `--tenant`, for the before/after)

| Path | Contents |
|---|---|
| `result.json` | The `ReplayResult`: status, outcome, outputs, per-step resolving tier and timing, failure with `step_id` / expected / observed. |
| `events.jsonl` | Every action, step completion, recovery and policy decision. |
| `ax/<step_id>.json` + `.png` | The observation the step's postcondition held on, and a screenshot with PII nodes masked. |
| `failure.json` + `failure.png` | Only on failure: the state the run stopped in. |

**Handoff** — `evidence/20260915T181841-180acf/` and `evidence/20260915T182422-3bfc25/`

| Path | Contents |
|---|---|
| `interventions/<n>/before.json`, `after.json` | Observation when control was handed to the human, and when it was handed back. |
| `interventions/<n>/diff.json` | What the human changed, as an AX diff. Their actions are never recorded as steps. |
| `interventions/<n>/before.png`, `after.png` | Masked screenshots. |
| `interventions/<n>/resolution.json` | The operator's decision — `retry` from a named step, `approve` (one-step risky approval), or `abort` — and their note. |

## Tests

```bash
uv run pytest
```

Chromium must be installed (see Setup). No API key, no running server:
`test_playwright_web.py`, `test_replay_engine.py` and `test_discovery_live.py`
start MemberServe in-process and drive headless Chromium; everything else runs
against in-memory fixtures (`tests/fixtures.py`).

| Required coverage | Where |
|---|---|
| Resolver: each tier in isolation, ambiguity falls through, unresolvable raises, resolving tier recorded | `test_resolver.py` |
| Error classification: each detector → business outcome / recoverable / hard failure | `test_classify.py` (fixture pages), `test_replay_engine.py` (live app, every fault) |
| Redactor: PII and secrets stripped from every egress path, including the model prompt | `test_redactor.py`, `test_discovery_loop.py::test_nothing_sensitive_leaves`, `test_compile.py::test_inlined_value_is_refused`, `test_replay_evidence.py`, `test_escalation.py::test_handoff_evidence_is_written_through_the_redactor` |
| Compile: dead ends pruned, values parameterized by name | `test_compile.py` |
| Schema round-trip without loss | `test_schema_roundtrip.py` |

## Invariants and where they are enforced

The twelve invariants from `CLAUDE.md`, with the code that holds each one and the
test that would break if it stopped.

| # | Invariant | Enforced in | Guarded by |
|---|---|---|---|
| 1 | No CSS selector, XPath or coordinate in an artifact | `schema/capability.py` — `TargetDescriptor` has no field that could hold one | `test_schema_roundtrip.py::test_no_locator_escape_hatch_in_the_schema` |
| 2 | No fixed delays; waits are state predicates | `replay/wait.py`, `surface/playwright_web.py` — deadline loops over `observe()` | `test_replay_engine.py::test_hung_request_times_out_on_the_step_budget` |
| 3 | Locator ambiguity is a hard failure | `surface/resolver.py` — `Ambiguous` after every signal, never first match | `test_resolver.py::test_ambiguous_after_all_signals_raises` |
| 4 | Every action goes through `session.act()` | `replay/session.py` — control token then policy, before dispatch | `test_session.py::test_every_action_passes_through_session_act` |
| 5 | Replay makes zero LLM calls | `replay/` imports nothing from `discovery/` | `test_replay_engine.py` runs with no model object in scope |
| 6 | Everything bound for log, artifact, evidence or prompt is redacted | `policy/redactor.py`, applied in `replay/events.py`, `replay/evidence.py`, `discovery/loop.py`, `discovery/transcript.py`, `escalation/evidence.py` | the redactor rows in the table above |
| 7 | No secret or raw PII persisted; parameters referenced by name | `compiler/steps.py` refuses inlined values; `schema` binds steps to declared inputs | `test_compile.py::test_inlined_value_is_refused`, `test_schema_roundtrip.py::test_step_referencing_undeclared_input_is_rejected` |
| 8 | Every step has a postcondition | `schema/capability.py` — required field | `test_schema_roundtrip.py::test_every_step_has_a_postcondition` |
| 9 | Nothing web-specific crosses the `Surface` port | `surface/graph.py` — `Observation` carries an opaque `location`, no URL/DOM/Playwright types | `test_playwright_web.py::test_graph_carries_nothing_web_specific` |
| 10 | Action schemas are OpenAI strict-mode compatible | `discovery/strict_schema.py` | `test_discovery_schema.py::test_schemas_are_strict_mode_compatible` |
| 11 | Compile is deterministic, no model | `compiler/` imports transcript types from `discovery/`, never `Model` | `test_compile.py::test_compile_is_deterministic_and_round_trips` |
| 12 | Risky-class steps never execute unattended | `policy/allowlist.py` — `ApprovalRequired` at the chokepoint | `test_policy.py::test_risky_step_halts_without_approval`, `test_discovery_loop.py::test_risky_turn_halts_before_dispatch`, `test_escalation.py::test_approve_runs_the_risky_step_exactly_once` |
