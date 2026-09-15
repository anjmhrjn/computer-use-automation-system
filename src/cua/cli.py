from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pydantic import ValidationError

from .replay import MissingParameter, replay
from .schema import AppProfile, Capability, ReplayStatus, dumps
from .surface import PlaywrightWebSurface

DEFAULT_TARGET = "http://127.0.0.1:5000"


def _load(path: Path) -> Capability | None:
    try:
        raw = path.read_text()
    except OSError as exc:
        print(f"error  cannot read {path}: {exc}", file=sys.stderr)
        return None

    try:
        return Capability.model_validate_json(raw)
    except ValidationError as exc:
        print(f"invalid  {path}", file=sys.stderr)
        for error in exc.errors():
            location = ".".join(str(part) for part in error["loc"]) or "<root>"
            print(f"    {location}: {error['msg']}", file=sys.stderr)
        return None


def _load_profile(artifact: Path, app_id: str) -> AppProfile | None:
    """Interstitial knowledge lives next to the artifacts, one file per app. A
    missing profile is an error, not a run without detectors."""
    path = artifact.resolve().parent / "apps" / f"{app_id}.json"
    try:
        raw = path.read_text()
    except OSError as exc:
        print(f"error  no app profile for {app_id!r} at {path}: {exc}", file=sys.stderr)
        return None
    try:
        return AppProfile.model_validate_json(raw)
    except ValidationError as exc:
        print(f"invalid  {path}", file=sys.stderr)
        for error in exc.errors():
            location = ".".join(str(part) for part in error["loc"]) or "<root>"
            print(f"    {location}: {error['msg']}", file=sys.stderr)
        return None


def _validate(path: Path) -> int:
    capability = _load(path)
    if capability is None:
        return 1

    outcomes = ", ".join(o.name for o in capability.outcomes)
    print(f"ok  capability {capability.capability_id} v{capability.version}")
    print(
        f"    schema_version {capability.schema_version}  "
        f"target {capability.target.app_id} {capability.target.app_version}"
    )
    print(
        f"    {len(capability.steps)} steps, "
        f"{len(capability.inputs)} inputs, "
        f"{len(capability.outputs)} outputs, "
        f"outcomes: {outcomes}"
    )
    return 0


def _parse_params(pairs: list[str]) -> dict[str, str] | None:
    params: dict[str, str] = {}
    for pair in pairs:
        name, sep, value = pair.partition("=")
        if not sep or not name:
            print(f"error  --param expects name=value, got {pair!r}", file=sys.stderr)
            return None
        params[name] = value
    return params


def _replay(path: Path, pairs: list[str], target: str) -> int:
    capability = _load(path)
    params = _parse_params(pairs)
    if capability is None or params is None:
        return 2
    profile = _load_profile(path, capability.target.app_id)
    if profile is None:
        return 2

    try:
        with PlaywrightWebSurface(target) as surface:
            result = replay(capability, params, surface, profile)
    except MissingParameter as exc:
        print(f"error  {exc.expected}; {exc.observed}", file=sys.stderr)
        return 2

    print(result.model_dump_json(indent=2))
    return 1 if result.status is ReplayStatus.failed else 0


def app() -> int:
    parser = argparse.ArgumentParser(prog="cua")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser(
        "validate", help="validate a capability artifact against the schema"
    )
    validate.add_argument("path", type=Path)

    subparsers.add_parser("schema", help="print the capability JSON Schema")

    run = subparsers.add_parser("replay", help="replay a capability against a live app")
    run.add_argument("path", type=Path)
    run.add_argument(
        "--param", action="append", default=[], metavar="NAME=VALUE", help="capability input"
    )
    run.add_argument("--target", default=DEFAULT_TARGET, help="base URL of the app")

    args = parser.parse_args()
    if args.command == "validate":
        return _validate(args.path)
    if args.command == "replay":
        return _replay(args.path, args.param, args.target)
    sys.stdout.write(dumps())
    return 0


def main() -> None:
    raise SystemExit(app())


if __name__ == "__main__":
    main()
