# Computer-Use Automation System

## What this is
A computer-use automation system: an LLM discovers how to do a task in a legacy UI once, the run is compiled into a typed reusable capability artifact, and that artifact is replayed deterministically without the LLM in production, with error handling, human escalation, and safety guardrails.

## Non-goals
queues, Docker Compose, Postgres, Celery, a real operator console, desktop surface adapters (including mocks), multi-tenant plumbing beyond the overlay resolver.

## Stack
| Decision | Choice | One-line defense |
|---|---|---|
| Language | Python 3.11+ | Pydantic gives typed artifacts + JSON Schema + LLM tool schemas from one model definition |
| Automation | Playwright (headed) | Used as transport, not as a locator library; headed is required for live-session human handoff |
| Perception | Accessibility tree via CDP; screenshot as evidence/disambiguation only | AX tree exists on desktop too — it's the seam to non-web surfaces |
| Target app | Self-built hostile legacy app ("MemberServe 3.1") | Only way to reproducibly trigger timeouts, permission denials, not-found, interstitials |
| Model | OpenAI behind a `Model` port; structured outputs for the action schema | Provider is one env var, not an architectural choice; strict JSON schema is the only model property the loop depends on |
| Architecture | Single process, CLI (`discover`, `replay`, `serve`), ports/adapters | Brief explicitly penalizes premature scaling infra |
| Storage | JSON files on disk | No database. Same reason |

## Repo layout
```
src/
  cua/
    schema/        Pydantic types: Capability, Step, TargetDescriptor, ReplayResult, InterventionRequest
    surface/       Surface port + PlaywrightWebSurface + tiered resolver. No web types cross the port.
    replay/        Replay engine, outcome detectors, error classification
    discovery/     Agent loop, Model port, OpenAI adapter, transcript fixture mode
    compiler/      transcript -> Capability
    policy/        Allowlist, risk classes, redactor
    escalation/    Control token, intervention requests, resume
    console/       FastAPI mock operator console
    cli.py         discover | replay | serve
app/             MemberServe 3.1 + variant-b (Flask). Scaffolding, not a deliverable.
artifacts/       Saved capabilities (JSON) and tenant overlays
evidence/        One directory per run: structured log, screenshots, AX snapshots, transcript
docs/decisions.md
tests/
```

## Core concepts
- **Surface** — anything we can perceive and act on. A port with two methods: `observe()` returns an element graph, `act()` performs one action on one resolved node. A browser is one implementation.
- **Discovery** — an LLM-driven run against a live surface that figures out how to accomplish a goal for the first time. Expensive, non-deterministic, run once.
- **Transcript** — the raw record of a discovery run, including dead ends. Not a deliverable and never replayed directly.
- **Capability** — the typed, versioned contract compiled from a successful transcript. Declares inputs, outputs, outcomes, ordered steps, and a checkpoint. This is the artifact.
- **Replay** — executing a capability with supplied parameters, with no model in the decision loop. This is the production path.
- **TargetDescriptor** — how a step identifies the control it acts on. Several independent signals recorded at discovery, resolved in ranked order at replay. Never a CSS selector, XPath, or coordinate.
- **Checkpoint** — a predicate asserting we actually reached the expected state. Steps have postconditions; the capability has a final checkpoint.
- **Business outcome vs. failure** — "no such member" is a declared return value of the capability, not a crash. Failures are things that went wrong; outcomes are answers the caller asked for.
- **Control token** — the single flag saying who may act on a session: automation or human. Held by one party at a time, checked on every action.
- **Location** — an opaque string the Surface reports with every observation (the web adapter fills it from the URL path; a desktop adapter would use window/screen title). The policy allowlist matches patterns against it. This is the only place anything URL-derived is visible above the port.

## Invariants
Generated code must never violate these. If a task appears to require breaking one, stop and say so instead.

1. No CSS selector, XPath, or pixel coordinate appears in an artifact, ever.
2. No `sleep()`, `wait_for_timeout()`, or fixed delay anywhere in the repo. Waits are state predicates.
3. Locator ambiguity is a hard failure. If a resolution tier matches more than one node, fall to the next tier; never pick the first match.
4. Every action goes through the single `session.act()` chokepoint, which checks the control token and the policy before dispatching.
5. Replay makes zero LLM calls. No fallback, no "just this once."
6. Everything bound for a log, artifact, evidence file, or model prompt passes through the redactor first.
7. No secret, credential, token, or raw PII is persisted anywhere. Parameter values are referenced by name, not inlined.
8. Every step has a postcondition. No step is assumed to have succeeded because it did not raise.
9. Nothing web-specific crosses the `Surface` port: no URL, no DOM node, no Playwright type.
10. Action schemas stay OpenAI strict-mode compatible: all fields required, `additionalProperties: false`, no defaults, optional expressed as nullable.
11. Compile is deterministic. No LLM call in `compiler/`; transcript -> Capability is rules only.
12. Risky-class steps never execute unattended. Replay requires `--approve-risky` or escalates to a human; discovery blocks them unless the allowlist enables them explicitly.

## Commands
```bash
uv sync && uv run playwright install chromium

uv run python -m app.server                    # MemberServe 3.1 at :5000
uv run python -m app.server --variant b        # tenant variant at :5001

uv run cua discover --goal "<goal>" --target http://127.0.0.1:5000
uv run cua discover --from-transcript evidence/<run_id>/transcript.jsonl   # no API key needed
uv run cua replay artifacts/<capability>.json --param member_id=12345
uv run cua serve                               # mock operator console at :8000
```

## Code conventions
- Full type annotations. Pydantic v2 for anything crossing a boundary or hitting disk; dataclasses for internal-only structures.
- `cua/schema/` holds the artifact types. The LLM provider abstraction lives in `cua/discovery/` and is called `Model` — do not conflate the two.
- Errors are typed and classified, never bare `Exception`. An error that reaches a caller carries `step_id`, what was expected, what was observed.
- No silent fallbacks. If something cannot be resolved, verified, or classified, raise with context.
- Modules stay under ~300 lines. Split by responsibility, not by size.
- Docstrings only where the *why* is non-obvious. No restating the signature.
- Log structured events (JSON lines), not prose strings.

## Testing
Must have tests:
- Resolver tiers: each tier resolves correctly in isolation, ambiguity falls through, unresolvable raises, and the resolving tier is recorded.
- Error classification: each detector maps to the right class (business outcome / recoverable / hard failure) against fixture pages.
- Redactor: known PII and secret patterns are stripped from every egress path including the model prompt.
- Compile step: a transcript with dead ends produces the expected pruned, parameterized capability.
- Schema round-trip: capability serializes and deserializes without loss.

Not worth testing: the Flask app, the operator console, CLI argument wiring, log formatting.

## Build order & current state
Schema first, and replay before discovery — if replay works on a hand-authored artifact, discovery only has to emit that shape. One item per chat, one commit each, always something runnable.

| # | Item | Status |
|---|---|---|
| 1 | Pydantic models + JSON Schema + one hand-written artifact. Artifact carries `schema_version`, `version`, and `target.app_version` | done |
| 2 | MemberServe 3.1 + seed data + fault injection + variant-b. No real login; session expiry is a fault-injected "session expired" interstitial. One iframe | done |
| 3 | `Surface` port + `PlaywrightWebSurface` + tiered resolver | todo |
| 4 | Replay engine against the hand-written artifact | todo |
| 5 | Error taxonomy, detectors, structured replay result | todo |
| 6 | Policy/allowlist + redactor through the action chokepoint | todo |
| 7 | Discovery loop + `--from-transcript` fixture mode | todo |
| 8 | Compile step: transcript -> Capability | todo |
| 9 | Escalation: control token, intervention request, console, resume. Human actions recorded as before/after AX diff + screenshots + operator note | todo |
| 10 | Evidence: discovery, clean replay, business-outcome replay, hard-failure replay | todo |
| 11 | README, tests, REPORT.md | todo |
| 12 | Stretch: variant-b via tenant overlay | todo |

Update this table at the end of every session.

## How to work with me
- Propose the approach before writing code. I want to see the plan and the trade-off, not a finished diff to react to.
- Small, reviewable diffs. One concern per change.
- Stop at the boundary of the current build item. Do not scaffold ahead into later items.
- When there is a real choice, give me the options and what each costs. Do not silently pick.
- If a simpler approach is correct, say so plainly, including when it contradicts something in this file.
- Do not add dependencies, abstractions, or config knobs that the current item does not need.
- Do not write `REPORT.md`.

## Decision log
Every non-obvious choice gets one paragraph in `docs/decisions.md`: what was chosen, what was rejected, why. Append at the end of any session that made a real decision. This file is the source for `REPORT.md`.