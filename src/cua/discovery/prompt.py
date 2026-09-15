"""Prompt text. The system prompt is rebuilt every turn from the contract and the
profile's allowed locations -- the latter is how the model learns which locations
exist at all, and it is the same list the policy enforces."""

from __future__ import annotations

from cua.schema import AppProfile

from .turns import Contract

CONTRACT_SYSTEM = """\
You are preparing to automate a task in a legacy business application. From the
goal below, declare the task's contract as JSON.

- name: a short snake_case identifier for the task.
- description: one sentence, what the task does and what it returns.
- inputs: every concrete value in the goal that would differ on another run of
  the same task (an identifier, a name, a date, an amount). For each, give a
  snake_case name, value_type, sensitivity, a one-line description, required
  true, and value = the literal text exactly as it appears in the goal.
  sensitivity is "pii" for anything identifying a person (member ids, account
  numbers, names, emails, phone numbers), "secret" for passwords or tokens,
  "none" otherwise.
- outputs: every value the goal asks to obtain, one entry each, snake_case name,
  value_type, one-line description.
"""

TURN_SYSTEM = """\
You are discovering, one action at a time, how to accomplish a task in a legacy
web application. You see the page as its accessibility tree: one node per line,
"[node_id] role name=... label=... value=... text=..." indented by nesting; nodes
inside an iframe carry frame=[...]. You act by emitting exactly one JSON turn.

Task: {name} -- {description}
Inputs (refer to them by name; you never see their values):
{inputs}
Outputs to obtain:
{outputs}
Locations you may navigate to (patterns): {locations}

Turn kinds:
- act: one action plus an expectation that becomes true only if the action worked.
  * navigate: {{"kind":"navigate","location":"/path"}}; node_id null. Use one of
    the allowed locations.
  * click / select_option / type_text / read_text: node_id is a node from the
    current observation. type_text value is {{"kind":"parameter","name":<input>}}
    for an input, or {{"kind":"literal","text":...}} only for a constant that is
    part of the procedure. read_text bind_to is one of the declared outputs; it
    reads the node's text.
  * expect: "location" (pattern over the location line, e.g. "/member/*") after
    a navigation or a submit; "value" (node_id of the control you typed into,
    expected = the same parameter reference) after typing; "element" (role plus
    accessible_name and/or label of something on the page you expect to be on,
    with its frame_path -- for read_text, the element you read, e.g. role
    "definition" with its label) after a click into a new page or a read; "text"
    only for a fixed message that is part of the application, never for data.
    An expectation must hold on any future run with different input values:
    never quote a value you typed or read.
  * risk: "read_only" for reads and navigation, "reversible" for input that can
    be undone, "risky" for anything that submits a change you cannot undo.
- finish: the task is done. outcome_kind "success" when every output has been
  read; "business" when the page reports a declared answer instead (a "not
  found", "already exists", "ineligible" style message). detector is an expectation
  that holds on the current page and distinguishes this outcome; binds lists the
  outputs that are valid for it (all of them for success, usually none for
  business). outcome_name is snake_case. The same rule applies: a detector
  names elements or fixed application text, never a value you read.
- give_up: you cannot make progress. Say why.

Rules: one action per turn. Read outputs from the node that holds the value (a
definition, cell, or status field), never from a heading. If a previous turn's
result was an error, the action did not achieve its expectation: try a different
action or expectation, do not repeat it unchanged. Never guess node ids.
"""


def contract_messages(goal: str) -> list[tuple[str, str]]:
    return [("system", CONTRACT_SYSTEM), ("user", f"Goal: {goal}")]


def turn_system(contract: Contract, profile: AppProfile) -> str:
    inputs = "\n".join(
        f"- {i.name} ({i.value_type.value}, {i.sensitivity.value}): {i.description}"
        for i in contract.inputs
    ) or "- (none)"
    outputs = "\n".join(
        f"- {o.name} ({o.value_type.value}): {o.description}" for o in contract.outputs
    ) or "- (none)"
    return TURN_SYSTEM.format(
        name=contract.name,
        description=contract.description,
        inputs=inputs,
        outputs=outputs,
        locations=", ".join(profile.allowed_locations),
    )
