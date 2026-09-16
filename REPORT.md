# 1. Architecture

An LLM discovers how to do a task in a UI once. The run is compiled into a typed capability artifact, and that artifact is replayed deterministically without the LLM. The model is used only in discovery and nowhere else.

`model discovers → transcript → compile (rules only) → capability → replay (zero LLM) → evidence`

```
Python + Pydantic        typed artifacts, JSON Schema, tool schemas from one definition
Playwright, headed       transport, not a locator library. Headed for live handoff
AX tree via CDP          exists on desktop too, the seam to non-web surfaces
Self-built MemberServe   only way to reproducibly inject timeout / 403 / interstitial
OpenAI behind Model port provider is one env var, not an architectural choice
One process, JSON files  reviewable, diffable artifacts. Brief penalizes scaling infra
```

**Two boundaries.** The Surface port has three methods - `observe()`, `act(action, node)` and `capture(mask)` - and everything above it only talks to those three. Nothing web-specific crosses it, no URL, DOM node or Playwright type. The one URL-derived thing is the location, an opaque string the allowlist matches against. `Session.act()` is the one path from the engine to the surface, and it checks the control token and the policy before dispatching.

**App profile.** Interstitials, the allowlist and `app_version` are properties of the app, so they live in `artifacts/apps/<app_id>.json`. Not in the capability, which would then be allowlisting itself, and not in code, where every new app would be a code change. The README maps each of the twelve invariants to the code and test that hold it.

# 2. Artifact schema

A capability is the file compile produces and the only thing replay reads. The contract - inputs, outputs, outcomes - is at the top, the steps come after.

```
schema_version, capability_id, version, target { app_id, app_version }
inputs[]    { name, value_type, required, sensitivity }
outputs[]   { name, value_type }
outcomes[]  { name, kind: success | business, detector, binds[] }
steps[]     { step_id, intent, action, target, risk, postcondition, timeout_ms }
checkpoint  detector predicate
```

The file is strict - no optional fields, no defaults, no unknown keys - and the same types are the model's tool schema in discovery, so there is one definition of an action and not two that can drift apart.

**How a step finds its control.** Each step points at its control with a `TargetDescriptor`, several independent clues instead of one locator - the kind of control, its name, label, nearby text, section, position among similar controls and frame. No field could hold a CSS selector, XPath or coordinate. The order clues are tried in lives in the resolver, not the artifact, so a better strategy benefits every saved artifact. Clues that leave more than one control are a failure, never the first match.

**Outcomes.** "Found" and "no such member" are both declared outcomes with a check that recognises them, and replay runs every check after every step, stopping when one matches. Branches inside the steps were rejected because compile would have to guess a branch from a recording that walked only one path, which rules can not do.

**The rest.** Every step has a postcondition and the capability a final checkpoint, both required. Steps refer to inputs by name, never by value, and each input declares a sensitivity (`pii`, `secret`, `none`) the redactor keys on. Step ids say what the step does, `enter_member_id`, `click_search`.

# 3. Determinism & error handling

**Determinism.** Replay makes zero LLM calls. The resolver starts with every control of the recorded kind in the recorded frame and narrows with each clue in a fixed order - name, section, label, nearby text, position - stopping the moment exactly one is left. A stale clue is skipped and traced.

**Three kinds of result.** A replay ends as `success`, `business_outcome` or `failed`. A business outcome is an answer the caller asked for, a failure is something that went wrong. Every fault MemberServe injects lands in one class. No such member is a declared outcome, returned as the answer. The maintenance interstitial is recoverable, replay clicks dismiss and re-runs the step, and after two dismissals it becomes `interstitial_persisted`. Everything else is a hard failure - `timeout`, `permission_denied`, `session_expired`, `target_drift`, `checkpoint_failed`, `policy_denied`, `approval_required`, `surface_error`. Permission denied is a failure on purpose, an "access denied" answer would look like a real one and never get a second look.

**UI drift.** `target_drift` is a control the clues no longer find. Compile also cross-checks every business detector against every page the success run passed through, so "any status element" can not fire on the found page. Recorded under `evidence/` - clean `20260915T184240-6afcac`, business `20260915T184249-f40405`, hard failure `20260915T184255-01415b`.

# 4. Heterogeneity & multi-tenant

**The seam.** Nothing above the Surface port knows it is a browser. The accessibility tree exists on desktop too, so a desktop adapter fills the same element graph, reports a window title as the location and masks by element bounds. Resolver, engine, policy, redactor and escalation are reused unchanged.

**Multi-tenant.** Tenant B is the same product with different labels. What was built is a tenant overlay, `artifacts/tenants/b.json`, a map from base string to tenant string. `cua replay --tenant b` rewrites the recorded clues before the run and touches nothing else, frame titles are a second map because a frame is a boundary and not a name. One overlay serves every capability for the app. The caller names the tenant. Nothing is detected and there is no falling back to an overlay when a step fails, that hides drift behind a lucky match.

**Drift.** The first event of an overlaid run, `overlay_applied`, lists which renames were used and which were not, and `target_drift` surfaces drift at replay time. Runs `20260916T103358-43b02b` with the overlay and `20260916T103359-b6838d` without.

# 5. Escalation & handoff

**When.** A failure goes to a human when the human can fix it, not when it is severe. An expired session, a hang, a notice that will not go away, a risky step awaiting approval. Permission denied escalates for a decision, the operator can only abort with a note. Drift, a failed checkpoint and adapter errors never escalate, an operator can not repair an artifact. A policy denial never escalates, that path would bypass the allowlist.

**The handoff.** The request carries the capability, step, failure text, location, parameter names and a masked screenshot, all redacted before it leaves the process. The console, `cua serve`, is a stdlib server that holds the replay's request open until the operator submits, no polling, no sleep. The console is a mock, the mechanism under it is real.

**Resuming.** The operator chooses `retry` from any step up to the failed one, `approve` for a one-step risky approval, or `abort`, which fails with the original failure kind plus the intervention record. There is no "continue from the next step" - a skipped read leaves an output unbound and a skipped click an unproven postcondition. Each handoff writes `interventions/<n>/` with snapshots before and after the human acted, a diff, masked screenshots and the operator's note (`20260915T182422-3bfc25`).

# 6. Safety

**Allowlist.** The app profile lists the locations automation may act on. `Session.act()` checks it before every action, for the current location and the destination of a navigate.

**Risk.** Every step is `read_only`, `reversible` or `risky`. Replay runs a risky step only with `--approve-risky`, otherwise it halts as `approval_required` before the action reaches the surface, and with a console attached the halt becomes a request. Discovery always runs unapproved. Recorded in `20260916T105604-a5f6b5`.

**Redaction.** Every `pii` or `secret` value is replaced by its parameter name wherever it appears, and a fixed pattern set catches keys, emails, SSNs and phone numbers from the page. It is applied at every sink - event log, artifact, evidence files, model prompt, console request - and screenshots are masked by the same rule. Stdout is not redacted, it is the caller's answer. The limits are that risk is self-declared by the model and outputs carry no sensitivity.

# 7. Cuts

**Mocked at a seam** - the operator console (stdlib, three routes), the desktop surface (port shaped for it, no adapter), real login (session expiry is fault-injected).

**Deliberately not built** - a draft/approved lifecycle where a reviewer adds outcomes discovery never reached (letting the model guess them was a silent fallback), version bumping, write actions in the target app, escalation during discovery, tenant detection or fallback, queues, database, containers.

**Next, in order** - version bump in `cua compile` refusing to overwrite without `--force`, an approval state gating unattended replay, a second Surface adapter to prove the port and not just describe it.
