"""transcript(s) -> Capability, by rules only (invariant 11). The same inputs
always produce the same artifact: every constant below is a constant, not a knob."""

from __future__ import annotations

import re

from pydantic import ValidationError

from cua.discovery import Contract, Finish
from cua.schema import (
    SCHEMA_VERSION,
    AppProfile,
    Capability,
    ParameterSpec,
    Provenance,
    SurfaceKind,
    TargetApp,
)

from .errors import NotCompilable
from .outcomes import business_outcome, success_outcome
from .run import DiscoveryRun
from .steps import PLACEHOLDER, compile_steps

COMPILER_VERSION = "1.0"
INITIAL_VERSION = "1.0.0"


def compile(primary: DiscoveryRun, extras: list[DiscoveryRun], profile: AppProfile) -> Capability:
    for run in (primary, *extras):
        if run.started.app_id != profile.app_id:
            raise NotCompilable(
                run.path, None, f"a run against {profile.app_id!r}", f"app_id {run.started.app_id!r}"
            )
        if _shape(run.contract) != _shape(primary.contract):
            raise NotCompilable(
                run.path,
                None,
                f"the success run's inputs and outputs {_shape(primary.contract)}",
                str(_shape(run.contract)),
            )
    steps = compile_steps(primary)
    success = success_outcome(primary)
    outcomes = [business_outcome(extra, primary, steps) for extra in extras] + [success]
    contract = primary.contract
    try:
        return Capability(
            schema_version=SCHEMA_VERSION,
            capability_id=f"{primary.started.app_id}.{contract.name}",
            version=INITIAL_VERSION,
            name=contract.name,
            description=contract.description,
            target=TargetApp(
                app_id=profile.app_id,
                app_version=profile.app_version,
                # The only surface there is; when a second adapter exists this
                # belongs in the profile next to app_version.
                surface_kind=SurfaceKind.web,
                location_pattern=_location_pattern(primary),
            ),
            inputs=[
                ParameterSpec(**{k: v for k, v in i.model_dump().items() if k != "value"})
                for i in contract.inputs
            ],
            outputs=list(contract.outputs),
            outcomes=outcomes,
            steps=steps,
            checkpoint=success.detector,
            provenance=Provenance(
                transcript_run_id=primary.started.run_id,
                discovered_at=primary.started.started_at,
                compiler_version=COMPILER_VERSION,
            ),
        )
    except ValidationError as exc:
        raise NotCompilable(
            primary.path, None, "a valid capability", str(exc).splitlines()[-1].strip()
        ) from exc


def _shape(contract: Contract) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Two runs of one goal word their contracts differently; what must agree is
    the typed interface."""
    return (
        [(i.name, i.value_type.value) for i in contract.inputs],
        [(o.name, o.value_type.value) for o in contract.outputs],
    )


def _location_pattern(primary: DiscoveryRun) -> str:
    """Where the capability ends up, with any input value's segment globbed:
    `/member/<param:member_id>` -> `/member/*`."""
    final = next(r for r in reversed(primary.turns) if r.ok and isinstance(r.response, Finish))
    return re.sub(re.escape(PLACEHOLDER) + r"[^>]*>", "*", final.location)
