# Decision log

One paragraph per non-obvious choice: what was chosen, what was rejected, why.

## Item 1 — artifact schema

**Business outcomes are capability-level detectors, not per-step branches.** A
capability declares its outcomes with a detector predicate each; replay evaluates
every detector after each step and halts the moment one fires. The rejected
alternative was per-step `on_outcome` edges, which is more expressive but puts
control flow inside the artifact — and the compiler (item 8) would then have to
infer branches from a linear transcript that only ever walked one of them, which
cannot be done by rules alone and would push an LLM into compile, breaking
invariant 11. A third option, evaluating outcomes only after the last step, was
rejected because a "no member found" page makes the remaining steps fail as hard
errors before the outcome is ever read, destroying the outcome-vs-failure
distinction the whole design rests on. Flat steps plus after-every-step detectors
keeps the compiler rules-only and still lets a run exit early with an answer.

**The artifact types are authored strict-mode compatible, and discovery reuses
them.** Every model forbids extra keys and no field carries a default; optional is
spelled nullable-and-required. This is deliberately verbose in hand-written JSON.
The alternative was a comfortable artifact schema plus a second, parallel set of
tool-schema models for the discovery loop — rejected because two definitions of
"what an action is" drift, and the drift shows up as a discovery run emitting
something the replay engine cannot execute. Two known gaps remain between what
pydantic emits and what OpenAI strict mode accepts: discriminated unions emit
`oneOf` + `discriminator` where strict wants `anyOf`, and `Literal` emits `const`
where strict wants `enum`. Both are mechanical transforms over the emitted schema.
They are deferred to item 7, where the discovery loop is the only consumer that
needs them; item 1 does not ship an unused transformer.

**`TargetDescriptor` records signals; the resolver owns tier order.** The
descriptor carries role, accessible name and match mode, label, nearby text,
container hint, ordinal, and frame path — but says nothing about which signal is
tried first. Encoding the tier order into each artifact was rejected: it would mean
that improving the resolution strategy invalidates every artifact already cut. With
order living in the resolver (item 3), re-ranking tiers is a code change that old
artifacts immediately benefit from. Ambiguity handling is likewise the resolver's:
a tier matching more than one node falls through to the next tier, never picks the
first match (invariant 3).

**Constraints this item places on MemberServe 3.1 (item 2).** The hand-written
artifact is the written spec for the app, so the app must satisfy it rather than
the reverse. It requires: a member search screen at `/members/search` with a
textbox accessibly named "Member ID" and a button named "Search", both inside a
form named "Member search"; a member record at `/member/<id>`; an iframe with a
`title` of "Member Record" — `frame_path` addresses frames by accessible name, so
an untitled iframe is unaddressable — containing a heading naming the record and a
"Coverage" group with "Plan Status" and "Renewal Date" definitions; and a "No
member found" state served at that *same* `/member/<id>` location. The last point
matters: the submit step's postcondition asserts the location, which must therefore
hold on both the found and not-found paths, leaving the outcome detectors — not a
failed postcondition — to distinguish them.

## Item 2 — MemberServe 3.1

**Session-level faults are armed out-of-band through a control endpoint.** The
harness (a test, or later the CLI) does `POST /__faults {kind, mode}` before or during
a run, and a `before_request` hook fires the armed fault on the next ordinary page
request. Two alternatives were rejected. Magic member ids ("90001 always times out")
need no plumbing but turn every fault into a record-level property, so nothing can
fail on the search page itself and a session-expired interstitial cannot land on the
`open_search` step. A startup flag (`--fault timeout`) is simplest but binds one fault
to one process, so the evidence runs in item 10 would need a server restart per
scenario. The endpoint keeps fault selection out of every URL the automation sees,
which is what keeps artifacts clean, at the cost of per-process register state that
two concurrent runs against one server could trample — acceptable for a
single-process CLI system. Record-level conditions are seed data, not faults: an
unknown id is "No member found", and one seeded member is `restricted`, served as a
403 at the same `/member/<id>` location so the submit postcondition still holds.

**The timeout fault is a gated hang, not a sleep.** A hung request blocks on a
`threading.Event` that is set when the fault is cleared via `DELETE /__faults/timeout`.
The rejected alternative was `time.sleep(N)` in `app/` with a documented carve-out from
invariant 2 on the grounds that the app is scaffolding. The gated form is barely more
code, keeps the invariant unconditional, and is more honest about what a timeout is:
the server never decides how long "too long" is, the replay engine's
`postcondition_timeout_ms` does. `mode` is ignored for `timeout` because a hang can
only end by being cleared.

**Two interstitials, one recoverable and one not.** "Session expired" offers only a
"Sign in again" link to a dead-end page — there is no real login, so there is nothing
automation could do, and that is the natural trigger for human escalation in item 9.
"Scheduled maintenance" has a Continue button that returns to the requested page,
giving item 5 a recoverable interstitial to classify and dismiss. A single
session-expired page with a "Start new session" button was rejected: it collapses the
two error classes into one and leaves item 9 without a scenario to escalate on. Armed
`until_cleared`, the maintenance notice re-fires after every Continue, which is the
"interstitial that will not go away" case for a retry ceiling; it is documented, not
special-cased.

**variant-b renames; it does not restructure.** Tenant B changes only accessible
names and labels (`Member No.`, `Find`, `Status`, `Renews On`, `Member File`, frame
title `Record`, group `Plan`) via a single label dictionary; routes, form nesting,
iframe, and the definition list are identical. Wider deltas — moving the search form
into the sidebar, or changing routes to `/members/lookup` — were rejected because they
would force the item-12 overlay to override containers, nearby text, and locations as
well as names, growing its schema to cover cases no tenant has asked for. As built,
the base artifact fails against variant-b exactly once, at the `open_search`
postcondition, for the right reason: the textbox is not named "Member ID".

**Hostile-legacy traits are deliberate and minimal.** Nested-table chrome, element ids
regenerated on every render, a site-wide "Search" button on every page next to the
form's own "Search" button, and the coverage data inside an iframe. Each exists to
exercise one thing the resolver must get right (no stable ids; ambiguity that only a
container tier can break; frame addressing by title). Nothing else was added.

**No write action in MemberServe; the risky-step guardrail is shown at the
chokepoint instead.** Invariant 12 needs evidence, not just a paragraph, but adding a
"create sub-account" screen plus a second capability and confirmation flow to get it
is breadth the target task does not need. The policy check in `session.act()` runs
before dispatch, so a risky step never reaches the surface unless approved — which
means the block can be demonstrated with a fixture copy of the lookup artifact whose
step is re-classed `risky`, replayed without `--approve-risky`: the engine halts at
that step and emits an intervention request, and the app never has to be able to do
the thing. That is a unit test in item 6 and one extra evidence run in item 10, both
against the artifact that already exists. The branch that is *not* demonstrated — a
human taking over and completing a risky write — is described in the report rather
than built, because demonstrating it requires a write feature the task does not have.
Delivered as `artifacts/…risky_fixture.json` (the compiled artifact, `click_search`
re-classed) and two runs: `20260916T105604-a5f6b5` halts unattended,
`20260916T105633-8418dd` raises the intervention request and the operator aborts.

## Item 3 — Surface port, Playwright adapter, tiered resolver

**Tiers narrow cumulatively; a signal that would leave zero is skipped.** The
resolver starts with every node of the descriptor's role in its frame and intersects
with each recorded signal in ranked order — accessible name, container, label, nearby
text, then ordinal — stopping at the first that leaves exactly one. The rejected
reading of "fall to the next tier" was independent tiers: try name alone, then
container alone, and so on. It cannot resolve the hand-written artifact, because no
single signal separates the site-wide "Search" button from the form's; only
name-and-container does. A signal that matches nothing is skipped rather than fatal so
that one renamed label does not kill a target the remaining signals still pin down;
the skip is recorded in the trace, so a later item can surface "resolved, but a
recorded signal no longer holds" as a drift warning. More than one node after every
signal is `Ambiguous`, never the first match (invariant 3). `ordinal` is a 0-based
index in document order over whatever survives the other signals; it is an explicit
recorded choice, not a tie-break, and is only consulted last.

**`frame_path` is a scope, not a tier.** Resolution never widens to another frame
when the named one has no match: the same label in a different frame is a different
control, and an artifact that silently crossed frames would read the wrong record.
Container and nearby-text lookups are likewise confined to the node's own frame. This
means variant-b's renamed iframe (`Record`) fails the base artifact outright, which is
what the item-12 overlay exists for.

**Acting on an AX-resolved node: stamp a one-off attribute, then a Playwright
locator.** Playwright exposes no way to build a handle from a CDP backend node id.
The adapter sets `data-cua-node=<uuid>` on the chosen node over CDP and asks each
frame for that attribute; the frame answering with exactly one match owns it, and
Playwright's actionability checks do the waiting. The rejected alternative was raw CDP
input — `DOM.getBoxModel` plus `Input.dispatchMouseEvent` — with no selector string
anywhere. It costs roughly four times the code, re-implements visible/enabled/stable
checks, and has to compose iframe offsets by hand, which is a classic source of
off-by-a-frame clicks. The tag is an internal handle on a node the accessibility-tree
resolver already chose; it is not a locating strategy, never appears in an artifact,
and never crosses the port, so invariants 1 and 9 hold as written.

**Surface actions carry values; the schema's `ValueRef` never reaches the port.**
`cua/surface/actions.py` is a second, tiny set of action types with concrete strings.
The alternative — passing the schema `Action` plus a parameter table through the port —
was rejected because it puts parameter resolution inside every adapter and hands
adapters a table of values they have no reason to see. As built, the only place a real
parameter value exists is in memory on its way into one `act()` call, which is what
invariant 7 wants. `ReadText` still goes through `act()` even though the text is
already in the observation, so that item 6's chokepoint sees every action, reads
included.

**Node ids are scoped to one observation, deliberately stricter than Chrome.** CDP
keeps accessibility node ids stable for the life of a document, so a node observed
before an action would usually still be valid after it. The adapter nonetheless
prefixes ids with an observation sequence number, so anything from an earlier snapshot
raises `StaleNode`. This forces the observe → resolve → act order on every step and
makes it impossible to act on an element resolved before a previous action changed the
page. The cost is one extra `observe()` per step in the replay loop; against a local
app that is milliseconds, and it is the same observation the postcondition needs.

**`nearby_text` is a document-order window.** A nearby string matches if it appears
in the text of any node within ±15 positions in the same frame (widened from 10 in item 4, when strict postcondition evaluation first exercised the signal against the real tree: nested-table chrome puts the "Find a member" heading 12 nodes before the textbox). Structural
definitions — "within the same landmark" or "within the same table row" — were tried
on paper and rejected: "Find a member" is a heading *outside* the search form, so any
container-based rule excludes exactly the case the artifact records. The window is a
heuristic and is ranked as the weakest signal, tried after name, container, and label.

**Adapter details learned from the tree, recorded so they are not re-learned.**
`Accessibility.getFullAXTree` returns nodes breadth-first, so document order is
recomputed by walking `childIds`. Chrome's `LayoutTable*` roles are *not* ignored
nodes; they stay in the graph as ancestors. `InlineTextBox` nodes duplicate their
`StaticText` parent and are dropped. `name.sources` lists every candidate source; the
one that applied is the one carrying a `value`. A `definition` has an empty
accessible name and is labelled by the preceding `term` sibling, which the adapter
computes so the artifact's `label: "Plan Status"` has something to match.
`DOM.pushNodesByBackendIdsToFrontend` requires `DOM.getDocument` on the current
document first, and every navigation is a new document.

## Item 4 — Replay engine

**The `Session` chokepoint exists from the first replay, even though it only
forwards.** `Session.act()` is the one path from the engine to the surface; the
engine never holds a `Surface` reference of its own. Today the method forwards and
resolves parameter references, nothing more. Deferring it to item 6 was rejected
because the policy check and the item-9 control-token check are additions *inside*
one method when the seam already exists, and a rewrite of every action site when it
does not. It is the one abstraction invariant 4 names, so it is not scaffolding. A
fake-surface test asserts every action reached the surface through `Session.act`.

**Business-outcome detectors run on the observation that satisfied the step's
postcondition, never during the wait.** The postcondition is the proof that the
action's transition happened; evaluating detectors on any earlier snapshot risks
matching text from the page *before* the click. The alternative — also checking
detectors on every poll so an outcome page that never satisfies the postcondition
still yields the answer instead of a timeout — was rejected for that reason and
because it needs a precedence rule between "postcondition holds" and "detector
fires". The cost is a constraint on artifacts: a step whose outcome page can differ
must carry a postcondition that holds on every branch (the item-1 decision already
requires this of `submit_search`). The success outcome is checked only after the
last step and the checkpoint, because it binds outputs that do not exist earlier;
checkpoint and success detector are both required to hold, redundantly in the
current artifact, so that an artifact whose two predicates differ gets both checked.

**Predicates resolve strictly; actions resolve tolerantly.** The item-3 resolver
skips a recorded signal that would leave zero candidates, so a click survives a
renamed label. Applied to `element_present` that tolerance is wrong: "textbox named
Member ID is present" would degrade to "some textbox is present", and the
`open_search` postcondition would pass on variant-b, contradicting the item-2 decision
that the base artifact fails there. `resolve(strict=True)` turns the skip into
`Unresolvable` and never early-returns on a single candidate, so every recorded signal
is checked. A separate matcher for predicates was rejected: one definition of "does
this descriptor match this node" is enough, and the trace is the same shape either
way. Strict mode immediately caught a real drift in the hand-written artifact — the
`nearby_text` window was too narrow for the real tree — which tolerant resolution
had been hiding since item 3.

**Ambiguous is neither present nor absent.** `element_present` is false when two
nodes match (invariant 3: never pick one), and `element_absent` is also false, because
the element is evidently there. A postcondition that hits ambiguity therefore times
out with the trace in the error, rather than passing by accident in either direction.

**Failures raise; only outcomes return.** `replay()` returns a `ReplayResult` only for
a declared outcome — success or business. Anything else is a typed `ReplayError`
carrying `step_id`, expected, observed: `MissingParameter`, `TargetUnresolved`,
`PostconditionTimeout`, `CheckpointFailed`. Folding failures into the result now was
rejected because item 5 owns their classification and the shape would change twice.
`ReplayResult.outputs` holds exactly the outcome's `binds`; an outcome that binds an
output no step read is a `CheckpointFailed`, not a silently shorter mapping.

**The wait is a poll of `observe()` with a monotonic deadline.** No sleep between
polls (invariant 2): each iteration is a full accessibility-tree read, and the
adapter's `observe()` already blocks on document readiness, so the loop is paced by
the surface rather than by a timer. Against a local app this settles in one or two
iterations; a remote surface would want the adapter to block longer, not the engine to
sleep.

## Item 5 — Error taxonomy, detectors, structured replay result

**Interstitial detectors live in a per-app profile, not in the artifact and not in
code.** `artifacts/apps/<app_id>.json` declares each interstitial the app can put in
front of a step — a detector predicate, the failure kind it maps to, and the control
that dismisses it, if any — using the same `Predicate` and `TargetDescriptor` types
the artifact uses. Declaring them in the capability was rejected because "session
expired" is a property of MemberServe, not of member lookup: every capability would
repeat the same three entries, and the item-8 compiler has no rules-only source for
an interstitial the discovery run never hit. Hard-coding them in the engine was
rejected because it puts app-specific strings in `cua/replay/` and makes every new
app a code change. The engine takes the profile as an argument; the CLI loads it from
beside the artifact and refuses to run without one rather than silently running with
no detectors.

**Interstitials are screened on every observation; business outcomes only on the
one that satisfied the postcondition.** The item-4 rule keeps outcome detectors off
the pre-action page. It cannot apply to interstitials, which are exactly the pages
that stop a postcondition from ever holding, so waiting for the postcondition before
checking them would turn every interstitial into a `timeout`. Screening the pre-action
observation as well matters for the 403: "Access denied" is served at `/member/<id>`,
so `submit_search`'s location postcondition holds on it, and without screening that
observation the failure surfaces one step later as `target_drift` on
`read_plan_status`. Screening first attributes it to `submit_search` as
`permission_denied`, which is the truth.

**Recovery is dismiss, then re-run the step; the ceiling is two dismissals.** After
clicking the profile's `dismiss` control the engine goes back to the step's own
observe, because the notice replaced the response and the step's effect is unproven.
Continuing to wait for the postcondition was rejected: it happens to work for
`open_search` (Continue lands on the requested page) and never for a step whose
request the notice ate. Re-running `submit_search` after a dismissal re-clicks an
emptied form and times out — honestly, with the recovery on the trace. A retry that
walks back to an earlier step was rejected as control flow the artifact does not
declare. The ceiling is a module constant, not a knob; hitting it is
`interstitial_persisted`.

**`replay()` returns a classified result; only caller errors raise.** `ReplayResult`
carries `status` (`success` / `business_outcome` / `failed`), a nullable `outcome`,
and a nullable `Failure` with `kind`, `step_id`, expected, observed, and a validator
tying the three together. Recovered interstitials are `Recovery` entries on the step
trace; a `Failure` is by definition unrecovered. Keeping the item-4 raise-and-catch
shape was rejected because every consumer — CLI now, evidence in item 10, the
console in item 9 — would rebuild the same record from an exception, and a failed run
would have no step traces at all. `MissingParameter` still raises: nothing ran.
`timeout` gets no automatic re-action; re-performing a step whose action may have
landed is worse than a clean failure.

**Permission denied is a hard failure, not a business outcome.** It is a fault class
the brief names and the natural escalation trigger for item 9. Declaring
`access_denied` as an outcome would give the caller a clean answer that is
indistinguishable from a legitimate one and never escalates.

**`act()` is dispatch; `observe()` is the only readiness wait, and it is bounded.**
Playwright's `goto`, `click` and `wait_for_load_state` default to a 30 s wait and
raise a Playwright type, so the timeout fault was surfacing after 30 s as a web
exception above the port (invariant 9) instead of on the step's own budget. The
adapter now bounds every wait with `ready_timeout_ms` and reports "not settled yet"
as `SurfaceNotReady`; the engine's poll loop treats that as "not yet" until the step
deadline says "never". Two things learned the hard way. Chrome suspends renderer-bound
DevTools commands, with no timeout, while a navigation is pending — `Runtime.evaluate`,
`DOM.*`, `Accessibility.*`, even `Page.getFrameTree` block until the hung request
answers — so the adapter tracks in-flight navigation requests from Playwright's
request/framenavigated/requestfailed events and never touches CDP while one is
outstanding; it blocks on the `framenavigated` event instead, under the same bound.
And Playwright 1.63's `no_wait_after=True` makes a form-submitting click time out
even when the navigation completes, so the default post-click wait is kept and a
`TimeoutError` from an action is read as "dispatched, navigation pending" when a
navigation request is outstanding and as `ActionNotApplicable` otherwise.

## Item 6 — Policy, allowlist, redactor

**The location allowlist is in the app profile, not the artifact.** `AppProfile`
gains `allowed_locations`, fnmatch patterns over the opaque location string the
surface reports, and `Session.act()` refuses any action while the surface is
elsewhere — or, for a navigate, any destination elsewhere. Trusting the capability's
own `target.location_pattern` was rejected: a compiled artifact would be
allowlisting itself, and a discovery run that wandered could emit `*`. A third file
(`policy.json`) was rejected as a second per-app file for one list; the profile is
already the operator-owned, per-app, loaded-beside-the-artifact place. The list must
name every location the app legitimately puts a control at, including where its own
interstitials land: MemberServe re-fires an `until_cleared` maintenance notice on the
`POST /continue` that dismisses it, so `/continue` is on the list. Exempting dismiss
clicks from the check instead was rejected because it makes the profile's own
`dismiss` descriptor the one action the guardrail does not see.

**Denials are failures on the result; `approval_required` is the seam for item 9.**
A risky step without `--approve-risky` halts as `FailureKind.approval_required` at
that step, with the step traces before it intact; an off-allowlist action halts as
`policy_denied`. Both are refused *before* dispatch, so the surface never sees the
action — the fake-surface tests assert an empty action list, not just a failure
kind. Raising instead was rejected under the item-5 rule that only caller errors
raise. Building an `InterventionRequest` now was rejected as scaffolding into item
9; when it exists, `approval_required` is the halt point it turns into a handoff.

**`act()` takes the risk class explicitly.** The engine passes `step.risk`; the
interstitial dismiss passes `reversible`, because clicking the profile's own
Continue is not the step's action. Holding "current step" on the session instead was
rejected: the policy check should be a pure function of the action, the risk, the
location and the policy, testable without an engine. The session does hold the last
observed location and the current `step_id`, both for the event log.

**Redaction is keyed by input sensitivity and bounded at token edges.** The redactor
is built from the run's own parameters: every `pii`/`secret` input's value is
replaced by `<param:name>` wherever it appears, longest value first, with
`(?<!\w)…(?!\w)` around it so a value of `"1"` does not shred every digit. `none`
inputs are left alone, because a plan name or a status is exactly the kind of value
that also appears legitimately in outputs. A fixed pattern set (bearer/api-key/`sk-`
tokens, email, SSN, US phone) catches shapes that arrived from the page rather than
from a parameter. Redacting every parameter regardless of sensitivity was rejected
for the same reason; the `Sensitivity` docstring already said it drives redaction.
Outputs are not redacted: they are the answer the caller asked for, and
`OutputSpec` carries no sensitivity until an artifact needs one.

**The event log cannot exist without a redactor, and redacts whole lines.**
`EventLog(stream, redactor)` serialises each event and passes the serialised line
through `redact` before writing, so there is no field a future caller can add that
bypasses invariant 6. On top of that, the `action` event carries the action kind,
the risk, the location and the target's role and name, never the typed or selected
text — so a `none`-sensitivity value never reaches the log even before redaction.
Failure strings on the result are redacted in the engine when the result is built,
so the `ReplayResult` is clean at source rather than at each consumer. The CLI
writes events to stderr and keeps stdout for the result JSON.

## Item 7 — Discovery loop and fixture mode

**The model declares the contract from the free-text goal, and sees each input
value exactly once.** `discover --goal "Look up ... for member 10001"` makes one
structured call that returns the contract: name, description, inputs (with the
literal value the model read out of the goal, its type, and its sensitivity) and
outputs. The redactor is built from those declared values before anything else is
written, so the goal itself lands in the transcript as `... member
<param:member_id>`, and every later prompt refers to inputs by name only: the
model types `{"kind":"parameter","name":"member_id"}` and the chokepoint
substitutes. A human-authored goal spec (inputs and outputs given up front) was
rejected as narrowing the brief — the point is that the LLM works the task out —
at the cost of the model seeing the raw value in that one declare call. Because a
sensitive value is `<param:name>` on disk, a fixture-mode run has to be given the
real value with `--param`; that is a feature, since it is also what makes the
transcript replayable for a different member.

**The model picks a node id; the loop derives the descriptor and proves it.**
The rendered accessibility tree carries an opaque `node_id` per line and the model
answers with one. `derive.describe()` builds a `TargetDescriptor` from that node by
rules — name, then the nearest named container, then the nearest preceding
heading/label as nearby text, then ordinal — adding one signal at a time until
`resolve(strict=True)` lands on exactly that node. The descriptor is therefore
proven replayable at the moment it is recorded, with the same resolver that will
replay it. Letting the model author descriptors was rejected: it can invent names,
it costs tokens, and it would still need the same round-trip. Deriving them at
compile time from raw observations was rejected because nothing would be
verified until first replay.

**Expectations are model-declared in a reduced form and upgraded to
predicates.** Every `act` carries an `expect` — `location`, `value`, `element`, or
`text` — and every `finish` a detector of the same shape; the loop turns it into a
full `Predicate` and polls it with replay's own `holds()` before the next turn.
`element` names a role plus accessible name and/or label (the first live run
showed that a `definition` has no accessible name and could only be named by its
label, which the shape initially lacked) and is upgraded by resolving it on the
page that appeared and deriving the descriptor from the match. A postcondition
that does not hold within the timeout is a dead end, fed back to the model as its
next result with the candidates it could have named. The transcript keeps the dead
end; item 8 prunes it.

**Two rules the loop enforces so the transcript stays replayable for other
inputs.** A `text` expectation that quotes a value just read or typed, or that
contains a `<param:…>` placeholder, is refused as a dead end before it is
evaluated. The first live run passed with `text_present "Active"` as the
postcondition for reading the plan status — true for member 10001, false for
every other member — and the compiled capability would have carried it; the prompt
now says so, but replayability across inputs must not depend on prompt compliance.
Likewise a turn identical to the previous, failed one is refused without being
executed, and three in a row halt the run as `stuck`: the second live run spent
sixteen turns re-issuing the same detector.

**Only observed outcomes are recorded.** One run walks one branch; the `finish`
turn names the outcome it reached and its detector must hold on the page the model
is looking at. Detectors for unreached outcomes are a human assertion, and letting
the model guess one was rejected as a silent fallback. A draft/approved lifecycle
on the artifact (compile emits a draft, a reviewer adds the other outcomes and
approves) is the right home for that and is a stretch goal, out of scope. The
business branch was discovered by a second run with an unknown id; merging the two
transcripts is item 8's problem, and both runs are kept under `evidence/`.

**Fixture mode is a `Model` adapter that re-binds node ids.** `TranscriptModel`
serves the recorded contract and turns; the surface, policy, derivation,
verification, and evidence writing all run for real, so `--from-transcript` proves
the recorded turns against the live app with no key. Node ids are scoped to one
observation and mean nothing in a new browser session, so each recorded `act` is
re-bound by resolving the descriptor the loop derived for it against the live
observation, through a `locate` callback on the prompt — the same identity replay
will use. Replaying recorded responses verbatim was the first design and failed on
the first live-app run. A recorded location that differs from the live one, or a
descriptor that no longer resolves, is `TranscriptDiverged`, not a best-effort
continue.

**Discovery does not dismiss interstitials, and a risky turn halts.** Any
interstitial the profile declares ends a discovery run as failed; dismissing it
would put "click Continue" in the transcript as a step. Risk is self-declared by
the model per action and discovery always runs with `approve_risky=False`, so a
`risky` turn halts as `approval_required` before dispatch (invariant 12); a
rule-based risk classifier is out of scope.

**What the model sees, and what is kept.** The prompt is the accessibility tree
only — no screenshot — rendered one node per line with layout-table wrappers
dropped (leaf layout cells kept, since legacy pages keep data there) and text shown
on leaves only; the first live-app rendering was three times longer and repeated
each container's text below it. Every run writes `evidence/<run_id>/` with the
transcript, the observation before and after every turn, and the literal prompt
text per turn, all through the redactor at the sink. The event log goes to stderr
as before; for the committed runs it was captured as `events.jsonl`.

## Item 8 — Compile: transcript → Capability

**One success run supplies the steps; each business-outcome run must be a prefix
of it and supplies one outcome.** `cua compile <success> [<business>…]` prunes the
success transcript to its ok `act` turns, in order, and carries each turn's derived
descriptor and postcondition over unchanged — they were proven at discovery with the
same resolver replay uses, so compile has nothing to re-derive. A business run's ok
`act` turns must equal the first N steps on action, target and postcondition (evidence
paths excluded, since each run writes its own); the first divergence is
`Incompatible`, naming the turn. Compiling the success run alone and hand-adding
business outcomes was rejected because the compiled artifact could then not produce
the `no_such_member` evidence run without a manual edit. Inferring branches from the
two runs' divergence point was rejected as control flow the artifact does not
declare (item 1). The prefix rule is what makes after-every-step detectors
(item 1) safe: replaying the success steps necessarily walks the page the business
detector was observed on.

**A business detector is cross-checked against every page the success run settled
on.** Discovery only proved the detector held on the page the model was looking at;
it never asked whether the detector also holds on the *found* page, and a detector
like "any `status` element" could. Compile loads the observation recorded before
each turn that follows an ok act — the page that act settled on — and evaluates the
detector with replay's own `holds()`; any hit is `Indiscriminate`. Trusting the
discovery-time check alone was rejected because the failure mode is a replay that
returns "no such member" for a member that exists — a wrong answer with a clean
status, the worst class of failure this system can have. The cost is that compile
reads the run directory, not just the transcript; the snapshots are already evidence
the run keeps. A `value_equals` detector cannot be cross-checked (its parameter value
is redacted in the transcript) and is refused as an outcome detector; an outcome is
an answer the page shows, not a control's value.

**`app_version` lives in the app profile.** The transcript records `app_id` and the
target URL, not a version, and the capability requires one. Adding it to `AppProfile`
was chosen over stamping it into `run_started` (the committed transcripts lack it,
so compile would need a fallback anyway) and over a `--app-version` flag (a knob two
compiles of the same transcript could disagree on). `surface_kind` stays a compiler
constant while the enum has one member; when a second adapter exists it belongs next
to `app_version` in the profile.

**Step ids are derived from the action, timeouts are the discovery budget, the
checkpoint is the success detector.** `navigate_<location>`, `enter_<param>`,
`click_<name>`, `read_<output>`, suffixed only on collision: readable in every log
line and stable across re-compiles, where positional ids say nothing and a slug of
the model's intent differs per run. `postcondition_timeout_ms` is discovery's
`EXPECT_TIMEOUT_MS` for every step — the budget the step was proven under; deriving
it from the recorded `elapsed_ms` bakes one local run's timing into the artifact. The
checkpoint duplicates the success detector because the transcript has no separate
final assertion and item 4 already treats the two as redundant by design. Every one
of these is a constant, not a flag: the same inputs must produce the same bytes
(invariant 11), and the CLI's output is byte-identical across runs.

**A `<param:…>` placeholder anywhere in a step or detector is a compile error.**
The redactor writes `<param:name>` wherever an input value appeared, so a
`navigate` to `/member/10001` or a detector quoting the member id would compile into
a step that sends the placeholder literally. Discovery already refuses text
expectations that quote a value (item 7); compile checks the serialized action,
target and postcondition of every kept turn regardless, because replayability across
inputs must not depend on the loop having caught it.

**Two runs of one goal word their contracts differently; compile requires the typed
interface to agree, not the prose.** The committed runs named the capability
`lookup_member_plan_status_and_renewal_date` and `lookup_plan_status_and_renewal_date`
with different descriptions and the same inputs and outputs. The success run's name
and description are the artifact's; the business runs must match on input and output
names and types only.

## Item 9 — Escalation: control token, intervention request, console, resume

**What escalates is decided by who can fix it, not by severity.** A failure hands
off to a human only when the *environment* blocked a correct artifact: `session_expired`,
`interstitial_persisted`, `timeout`, `approval_required` (a guardrail whose whole
meaning is "ask a human"), and `permission_denied`. The last one escalates for a
decision, not a fix — the operator can only abort, with a note — because a clean
terminal failure would hide the one case a supervisor should look at. `target_drift`,
`checkpoint_failed` and `surface_error` never escalate: an operator cannot repair an
artifact or an adapter, and a human clicking the right control is the `continue`
semantic rejected below. `policy_denied` never escalates on principle: an escalation
path past the allowlist is a bypass path. "Every failure escalates, the operator can
always abort" was the first draft and was rejected because it drags an operator into
failures that are the artifact owner's, and because it would make the allowlist
overridable at runtime. The table lives in `replay/classify.py` beside the failure
taxonomy, and a test requires every `FailureKind` to be placed in it explicitly.

**The operator resumes from a step they name; there is no `continue`.** A resolution
is `retry` from any step up to and including the failed one, `approve` (re-run the
failed step with a one-step risky approval; valid only for `approval_required`), or
`abort` (the run fails with its *original* failure kind plus the intervention record —
no new kind). Retrying only the failed step was rejected because a session that
expires mid-form leaves the operator unable to re-fill a value they are deliberately
not shown; resuming from `open_search` is the honest recovery. `continue` — the human
performed the step, resume at the next — was rejected: a skipped read leaves an output
unbound, MemberServe has no write to exercise it, and it is the risky-write branch
item 2 chose to describe rather than build. Every re-run step proves its postcondition
again; traces from re-run steps are appended, not replaced, so the result shows the
detour. Operator input is untrusted: an unknown `resume_from`, or `approve` on the
wrong failure kind, is `EscalationError`, a caller error like `MissingParameter`.

**The console is stdlib, out of process, and the wait is a held HTTP request.**
`cua serve` is a `ThreadingHTTPServer` with three JSON routes and two HTML pages; a
paused replay POSTs its request and then GETs the resolution, which the server holds
open on a `threading.Event` until the operator submits the form. No sleep, no file
polling (invariant 2), and the run blocks for exactly as long as the human holds the
browser. FastAPI + uvicorn, named in the stack table, was rejected as two runtime
dependencies for three routes; an in-process console thread was rejected because it
dies with the run and leaves no `serve` command. The console shows only what the
request carries, and the request carries only redacted failure text, the redacted
location, and parameter *names* — which the end-to-end run enforced the hard way: the
first version leaked `/member/20001` as the location, and the location is now
redacted where the failure text is, before the request leaves the process.

**Screenshots are masked by the redactor's rule, which resolves item 9 against
invariant 7.** Pixel evidence cannot pass through a text redactor, and a member record
on screen is PII that evidence readers — not just the operator, who already sees the
window — would otherwise receive. Three options were weighed: raw PNGs with a
documented carve-out (rejected: evidence is read by people with fewer privileges than
the operator, and the reviewer-versus-operator argument is exactly what the redactor
exists for), AX diff only (correct but drops the deliverable), and masking. The port
gains `capture(mask)`: the escalation handler passes every node whose name, value,
label or text the run's redactor would rewrite — innermost only, so a container's
subtree text never masks the page — and the adapter paints over each or raises, never
returning a partial image. Coverage therefore equals the AX tree's, on every surface;
a desktop adapter masks by the node bounds its accessibility API already exposes.
Learned in the live run: a `StaticText` is a DOM text node and cannot carry the tag
attribute, so the adapter tags the parent element — the mask is wider than the value,
never narrower — and the same fix covers a click aimed at a text node.

**The control token is a programming guard, not a failure kind.** `Session.token`
is checked first in `act()`, before the policy. A violation raises `ControlHeld`
(a `RuntimeError`) and is not classified, because the engine is blocked inside
`escalator.request()` for as long as a human holds the token; anything reaching the
check while it does is a bug, and a bug should not come back as a tidy result.
`observe()` and `capture()` are not gated, so the handoff can record the after-state.

**Replay gets an evidence directory only for handoffs; the rest is item 10.**
`evidence/<run_id>/interventions/<n>/` holds the redacted before/after snapshots, the
node diff, the resolution with the operator's note, and the two masked PNGs. A side
the surface could not observe — the page is exactly as hung as a `timeout` says — is
written as `<side>.unobservable`, not skipped. `events.jsonl` and `result.json` for
replay are not written here; item 10 owns the shape of replay evidence and this item
does not scaffold it. Discovery keeps halting on interstitials and risky turns
(item 7); escalating discovery is out of scope.

**Replay evidence is one directory per run, and the snapshot per step is the
observation that satisfied its postcondition.** `evidence/<run_id>/` holds
`events.jsonl` (the same redacted lines the CLI streams to stderr — `EventLog` fans
out to every stream it is given, so there is no second formatter), `result.json`,
`ax/<step_id>.json` + `.png` per completed step, `failure.json` + `.png` (or
`failure.unobservable`) when the run stopped, and `interventions/<n>/` from item 9
unchanged. The pre-action observation was rejected as the per-step record: the
resolver's choice is already in the trace (`resolved_tier`), and what a reviewer
needs to check is the state the engine *accepted* as "this step worked" — that is
the postcondition-satisfying observation, and the screenshot is taken of that page.
`result.json` passes the redactor although the result on stdout does not: stdout is
the caller's answer, the file is evidence, and evidence readers are the audience the
redactor exists for. Outputs not declared sensitive (`plan_status`) therefore stay
legible on disk, exactly as in the discovery snapshots. A step re-run after a handoff
overwrites its own files rather than versioning them; the trace list shows the
detour and the intervention directory holds the before/after. `ReplayResult` gains
`run_id` so a caller holding only stdout can find the directory; it is the one
schema change. Learned in the first live run: the typed value of an `<input>` is a
text node inside the control's user-agent shadow tree, which CDP refuses to tag, so
the adapter now climbs out of shadow trees to the host before stamping — the mask
lands on the `<input>` itself, wider than the value, never narrower. The
uncommitted item-9 run `20260915T182422-3bfc25` is left for the author to commit.

## Item 12 — variant-b via tenant overlay

**The overlay is a map of base string → tenant string, applied to descriptor signals
only.** `artifacts/tenants/<tenant_id>.json` carries `renames`, keyed by the strings
the base artifact records (`Member ID`, `Search`, `Plan Status`, …), and `replay`
rewrites `accessible_name`, `label`, `nearby_text`, container names and
`text_present` text wherever a `TargetDescriptor` or predicate appears — steps,
postconditions, outcome detectors, checkpoint — before the run starts. Whole-string
match only: rewriting inside a longer string would be a second matcher hidden behind
`name_match: contains`. Nothing else in the artifact is touched, so `step_id`s,
intents, locations, parameter names and `capability_id` are those of the base
artifact and the `ReplayResult` still cites it; the overlay is not a new capability.
The rejected shape was per-step target overrides keyed by `step_id`: it can express
structural drift, but item 2 fixed variant-b to renames only precisely so that no
tenant needs it, and it has to be authored per capability and breaks when a
recompile renumbers steps. A per-tenant string map is authored once per tenant and
serves every capability for the app.

**Frame titles are a second map.** The first tenant already broke the flat map:
variant-b retitles the record iframe `Record` but its heading `Member File`, and the
base artifact records both as `Member Record`. `frames` renames `frame_path`
entries and nothing else; `renames` never reaches a `frame_path`. This follows the
item-3 decision that a frame is a scope, not a name tier, and costs one dictionary
rather than a per-role or per-field addressing scheme. The alternative — accept the
limitation and note that the item-1 fixture's heading check is unexpressible — was
rejected because the compiled artifact only escaped it by chance (its checkpoint is
the `Renewal Date` definition, not the heading).

**The caller names the tenant; nothing is detected.** `cua replay --tenant b` loads
the overlay; a missing file or an `app_id` that does not match the artifact is an
error before anything runs. Two alternatives were considered. Detecting the tenant
from the surface — each overlay carrying an `identity` predicate evaluated on the
first observation — is attractive and would fit this file format without changing
it, but the caller of `replay` is an integrating backend, not a tenant, and it
already knows which tenant it is serving; the detection would re-derive a fact the
caller has, and add an ambiguity case (two overlays match) for no gain. Trying the
base names and falling back to each overlay on failure was rejected outright: it is
a silent fallback that replays steps twice and hides drift behind a lucky match.
Consequently `--tenant b` against the base app fails at `enter_member_id` with
`target_drift`, exactly as the base artifact does against variant-b.

**Unused renames are reported, not rejected.** Because one overlay serves every
capability for the app, a key a given capability never references is normal, not an
error; the compiled artifact never reads the `Coverage` group or either heading, so
three of tenant B's nine renames are unused on that run. The run's first event,
`overlay_applied`, lists `applied` and `unused` by key so the evidence shows exactly
what the overlay changed. Provenance stops there: `ReplayResult` did not gain a
`tenant` field, because the result is the answer to the caller's question and the
caller supplied the tenant; the events file is where "how the run was configured"
already lives.

## Future work — artifact versioning

**Every compile emits `version: "1.0.0"`, and `cua compile` overwrites whatever is
at `artifacts/<capability_id>.json`.** The compiler is a pure function of its
transcripts (invariant 11) and never reads the artifact it is about to replace, so
it cannot know whether one exists or what version it carried; the CLI then writes
unconditionally. The `version` field was put in the schema in item 1 so that an
artifact can be superseded without being confused with its predecessor, but nothing
in the pipeline sets it to anything else. Two things go wrong today: re-discovering
a goal after the app changes and compiling the new transcript replaces the old
artifact under the same id and version, while the replay evidence in `evidence/`
still cites that id and version; and `capability_id` is `<app_id>.<contract.name>`
with the name chosen by the model, so two unrelated goals that get the same
snake_case name collide silently. `--out` is the only escape hatch. This was left
as is because the single-capability target task never recompiles a changed goal.

**How to implement it, when it is needed.** The bump belongs in the CLI, not in
`compiler/`: compile stays deterministic and the version is a fact about the
artifact store, not about the transcripts. `cua compile` reads the existing file at
the target path (if any), compiles the new capability with a placeholder version,
and compares the two with `provenance` and `version` masked out. Identical → write
nothing and report "unchanged". Different only in steps, outcomes' detectors or
`target.app_version` → patch bump: same contract, same caller code, new procedure.
Different in `inputs`, `outputs` or the set of outcome names → minor bump if the
change is additive (a new optional input, a new output, a new business outcome),
major otherwise: the caller's code has to change. Bumping is refused when the
`capability_id` matches but the `target.app_id` differs — that is a collision, not a
successor — and the run tells the author to rename the contract. The first
artifact for an id is `1.0.0` as now. Replay does not need to change: the artifact
it is handed is self-describing, and `provenance.transcript_run_id` already says
which discovery produced it. What would need a decision is retention — whether the
superseded artifact is kept beside the new one as
`<capability_id>@<version>.json` so that old evidence stays reproducible, or
whether git history is enough. Until then, the cheap guard is `cua compile`
refusing to overwrite an existing path without `--force`.
