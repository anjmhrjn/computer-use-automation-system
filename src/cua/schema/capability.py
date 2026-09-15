from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import model_validator

from .action import Action, ReadText, SelectOption, TypeText
from .common import ParameterValue, RiskClass, Sensitivity, StrictModel, ValueType
from .predicate import Predicate
from .target import TargetDescriptor

SCHEMA_VERSION = "1.0"


class SurfaceKind(str, Enum):
    web = "web"


class OutcomeKind(str, Enum):
    success = "success"
    business = "business"


class TargetApp(StrictModel):
    app_id: str
    app_version: str
    surface_kind: SurfaceKind
    location_pattern: str


class ParameterSpec(StrictModel):
    name: str
    value_type: ValueType
    required: bool
    sensitivity: Sensitivity
    description: str


class OutputSpec(StrictModel):
    name: str
    value_type: ValueType
    description: str


class OutcomeSpec(StrictModel):
    """A declared answer the caller asked for, not a failure.

    Detectors are evaluated after every step; the first to fire ends the run with
    that outcome. `binds` names the outputs that are meaningful when it fires.
    """

    name: str
    kind: OutcomeKind
    detector: Predicate
    binds: list[str]
    description: str


class Step(StrictModel):
    step_id: str
    intent: str
    action: Action
    target: TargetDescriptor | None
    risk: RiskClass
    postcondition: Predicate
    postcondition_timeout_ms: int


class Provenance(StrictModel):
    transcript_run_id: str
    discovered_at: str
    compiler_version: str


class Capability(StrictModel):
    schema_version: Literal["1.0"]
    capability_id: str
    version: str
    name: str
    description: str
    target: TargetApp
    inputs: list[ParameterSpec]
    outputs: list[OutputSpec]
    outcomes: list[OutcomeSpec]
    steps: list[Step]
    checkpoint: Predicate
    provenance: Provenance | None

    @model_validator(mode="after")
    def _check_references(self) -> "Capability":
        errors: list[str] = []

        input_names = {p.name for p in self.inputs}
        output_names = {o.name for o in self.outputs}

        step_ids = [s.step_id for s in self.steps]
        if len(step_ids) != len(set(step_ids)):
            errors.append("step_id values must be unique")
        if not self.steps:
            errors.append("a capability must declare at least one step")

        successes = [o.name for o in self.outcomes if o.kind is OutcomeKind.success]
        if len(successes) != 1:
            errors.append(
                f"exactly one success outcome is required, found {len(successes)}"
            )

        outcome_names = [o.name for o in self.outcomes]
        if len(outcome_names) != len(set(outcome_names)):
            errors.append("outcome names must be unique")

        for outcome in self.outcomes:
            for bound in outcome.binds:
                if bound not in output_names:
                    errors.append(
                        f"outcome {outcome.name!r} binds undeclared output {bound!r}"
                    )

        for step in self.steps:
            action = step.action
            if isinstance(action, ReadText) and action.bind_to not in output_names:
                errors.append(
                    f"step {step.step_id!r} binds undeclared output "
                    f"{action.bind_to!r}"
                )
            value = None
            if isinstance(action, TypeText):
                value = action.value
            elif isinstance(action, SelectOption):
                value = action.option
            if isinstance(value, ParameterValue) and value.name not in input_names:
                errors.append(
                    f"step {step.step_id!r} references undeclared input "
                    f"{value.name!r}"
                )

        if errors:
            raise ValueError("; ".join(errors))
        return self
