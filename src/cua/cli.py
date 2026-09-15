from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pydantic import ValidationError

from .compiler import CompileError, compile, load
from .discovery import (
    DiscoveryStatus,
    MalformedTranscript,
    Model,
    ModelError,
    TranscriptModel,
    discover,
    read,
)
from .escalation import ConsoleClient, EscalationError
from .policy import Redactor
from .replay import MissingParameter, replay
from .schema import AppProfile, Capability, ReplayStatus, dumps
from .surface import PlaywrightWebSurface

DEFAULT_TARGET = "http://127.0.0.1:5000"
DEFAULT_APP = "memberserve"
DEFAULT_CONSOLE_PORT = 8000
ARTIFACTS_DIR = Path("artifacts")
EVIDENCE_DIR = Path("evidence")


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


def _load_profile(artifacts: Path, app_id: str) -> AppProfile | None:
    """Interstitial knowledge lives next to the artifacts, one file per app. A
    missing profile is an error, not a run without detectors."""
    path = artifacts.resolve() / "apps" / f"{app_id}.json"
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
    _summarize(capability)
    return 0


def _summarize(capability: Capability) -> None:
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


def _compile(transcripts: list[Path], out: Path | None, artifacts: Path) -> int:
    try:
        primary, *extras = [load(path) for path in transcripts]
    except (OSError, MalformedTranscript, CompileError) as exc:
        print(f"error  {exc}", file=sys.stderr)
        return 2
    profile = _load_profile(artifacts, primary.started.app_id)
    if profile is None:
        return 2
    try:
        capability = compile(primary, extras, profile)
    except CompileError as exc:
        print(f"error  {exc}", file=sys.stderr)
        return 2

    # The transcript was redacted on the way to disk; the artifact goes through the
    # fixed patterns once more on its own way there (invariant 6).
    text = Redactor(()).redact(capability.model_dump_json(indent=2)) + "\n"
    path = out if out is not None else artifacts / f"{capability.capability_id}.json"
    path.write_text(text)
    print(f"wrote  {path}")
    _summarize(capability)
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


def _replay(args: argparse.Namespace) -> int:
    capability = _load(args.path)
    params = _parse_params(args.param)
    if capability is None or params is None:
        return 2
    profile = _load_profile(args.path.resolve().parent, capability.target.app_id)
    if profile is None:
        return 2
    escalator = None if args.console is None else ConsoleClient(args.console)

    try:
        with PlaywrightWebSurface(args.target) as surface:
            result = replay(
                capability,
                params,
                surface,
                profile,
                approve_risky=args.approve_risky,
                log_stream=sys.stderr,
                escalator=escalator,
                evidence_dir=args.evidence_dir,
            )
    except MissingParameter as exc:
        print(f"error  {exc.expected}; {exc.observed}", file=sys.stderr)
        return 2
    except EscalationError as exc:
        print(f"error  {exc}", file=sys.stderr)
        return 2

    print(f"evidence  {args.evidence_dir / result.run_id}", file=sys.stderr)
    print(result.model_dump_json(indent=2))
    return 1 if result.status is ReplayStatus.failed else 0


def _serve(host: str, port: int) -> int:
    from .console import serve

    serve(host, port, lambda url: print(f"console  {url}", file=sys.stderr))
    return 0


def _discover(args: argparse.Namespace) -> int:
    params = _parse_params(args.param)
    if params is None:
        return 2
    model: Model
    if args.from_transcript is not None:
        try:
            model = TranscriptModel(read(args.from_transcript), params)
        except (OSError, MalformedTranscript, ModelError) as exc:
            print(f"error  {exc}", file=sys.stderr)
            return 2
        goal, app_id = model.started.goal, model.started.app_id
    else:
        if args.goal is None:
            print("error  --goal is required unless --from-transcript is given", file=sys.stderr)
            return 2
        if params:
            print("error  --param is only for --from-transcript; live discovery reads values from the goal", file=sys.stderr)
            return 2
        from .discovery.openai_model import OpenAIModel

        try:
            model = OpenAIModel()
        except ModelError as exc:
            print(f"error  {exc}", file=sys.stderr)
            return 2
        goal, app_id = args.goal, args.app
    profile = _load_profile(args.artifacts, app_id)
    if profile is None:
        return 2

    try:
        with PlaywrightWebSurface(args.target) as surface:
            result = discover(
                goal, surface, profile, model, args.evidence_dir, target=args.target, log_stream=sys.stderr
            )
    except ModelError as exc:
        print(f"error  {exc}", file=sys.stderr)
        return 2

    print(result.model_dump_json(indent=2))
    return 1 if result.status is DiscoveryStatus.failed else 0


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
    run.add_argument(
        "--approve-risky",
        action="store_true",
        help="allow risky-class steps to execute; without it they halt the run",
    )
    run.add_argument(
        "--console",
        metavar="URL",
        help="operator console to hand off to on an escalating failure; without it the run fails",
    )
    run.add_argument(
        "--evidence-dir", type=Path, default=EVIDENCE_DIR, help="where run evidence goes"
    )

    console = subparsers.add_parser("serve", help="run the mock operator console")
    console.add_argument("--host", default="127.0.0.1")
    console.add_argument("--port", type=int, default=DEFAULT_CONSOLE_PORT)

    find = subparsers.add_parser(
        "discover", help="let the model work out how to accomplish a goal against a live app"
    )
    find.add_argument("--goal", help="what to accomplish, in plain text, including the concrete values")
    find.add_argument(
        "--from-transcript",
        type=Path,
        metavar="PATH",
        help="re-run a recorded transcript's model turns instead of calling the model",
    )
    find.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="with --from-transcript: value for an input the transcript redacted",
    )
    find.add_argument("--app", default=DEFAULT_APP, help="app profile to load from artifacts/apps/")
    find.add_argument("--target", default=DEFAULT_TARGET, help="base URL of the app")
    find.add_argument("--artifacts", type=Path, default=ARTIFACTS_DIR, help="artifacts directory")
    find.add_argument("--evidence-dir", type=Path, default=EVIDENCE_DIR, help="where run evidence goes")

    build = subparsers.add_parser(
        "compile", help="compile discovery transcripts into a capability artifact"
    )
    build.add_argument(
        "transcripts",
        type=Path,
        nargs="+",
        metavar="TRANSCRIPT",
        help="the success run first, then any business-outcome runs of the same goal",
    )
    build.add_argument("--out", type=Path, help="where to write; default artifacts/<capability_id>.json")
    build.add_argument("--artifacts", type=Path, default=ARTIFACTS_DIR, help="artifacts directory")

    args = parser.parse_args()
    if args.command == "compile":
        return _compile(args.transcripts, args.out, args.artifacts)
    if args.command == "discover":
        return _discover(args)
    if args.command == "validate":
        return _validate(args.path)
    if args.command == "replay":
        return _replay(args)
    if args.command == "serve":
        return _serve(args.host, args.port)
    sys.stdout.write(dumps())
    return 0


def main() -> None:
    raise SystemExit(app())


if __name__ == "__main__":
    main()
