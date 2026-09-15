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
