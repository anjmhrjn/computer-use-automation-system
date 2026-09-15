from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pydantic import ValidationError

from .schema import Capability, dumps


def _validate(path: Path) -> int:
    try:
        raw = path.read_text()
    except OSError as exc:
        print(f"error  cannot read {path}: {exc}", file=sys.stderr)
        return 1

    try:
        capability = Capability.model_validate_json(raw)
    except ValidationError as exc:
        print(f"invalid  {path}", file=sys.stderr)
        for error in exc.errors():
            location = ".".join(str(part) for part in error["loc"]) or "<root>"
            print(f"    {location}: {error['msg']}", file=sys.stderr)
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


def app() -> int:
    parser = argparse.ArgumentParser(prog="cua")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser(
        "validate", help="validate a capability artifact against the schema"
    )
    validate.add_argument("path", type=Path)

    subparsers.add_parser("schema", help="print the capability JSON Schema")

    args = parser.parse_args()
    if args.command == "validate":
        return _validate(args.path)
    sys.stdout.write(dumps())
    return 0


def main() -> None:
    raise SystemExit(app())


if __name__ == "__main__":
    main()
