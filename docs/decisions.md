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
