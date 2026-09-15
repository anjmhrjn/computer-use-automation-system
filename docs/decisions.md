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
