from __future__ import annotations

import argparse
import secrets
from urllib.parse import urlsplit

from flask import Flask, Response, abort, redirect, render_template, request, url_for

from . import faults
from .data import Member, load_members
from .labels import DEFAULT_PORTS, VARIANTS


def create_app(variant: str = "a") -> Flask:
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant {variant!r}; expected one of {sorted(VARIANTS)}")

    app = Flask(__name__)
    app.config["MEMBERS"] = load_members()
    app.config["LABELS"] = VARIANTS[variant]
    app.config["FAULTS"] = faults.FaultRegister()
    faults.install(app, app.config["FAULTS"])

    @app.context_processor
    def inject_globals() -> dict[str, object]:
        # Fresh ids on every render: nothing stable for a selector to latch onto.
        return {"L": app.config["LABELS"], "ctl": lambda: f"ctl_{secrets.token_hex(4)}"}

    def find(member_id: str) -> Member | None:
        return app.config["MEMBERS"].get(member_id)

    @app.get("/")
    def home() -> Response:
        return redirect(url_for("search"))

    @app.get("/members/search")
    def search() -> str:
        return render_template("search.html", error=None)

    @app.post("/members/search")
    def submit_search() -> Response | tuple[str, int]:
        member_id = request.form.get("member_id", "").strip()
        if not member_id:
            return render_template("search.html", error="Enter a member id."), 200
        return redirect(url_for("member", member_id=member_id))

    @app.get("/member/<member_id>")
    def member(member_id: str) -> str | tuple[str, int]:
        found = find(member_id)
        if found is None:
            return render_template("not_found.html", member_id=member_id), 200
        if found.restricted:
            return render_template("forbidden.html", member_id=member_id), 403
        return render_template("member.html", member=found)

    @app.get("/member/<member_id>/record")
    def record(member_id: str) -> str:
        # The iframe document. Only ever loaded inside the frame on /member/<id>;
        # the same guards apply so a direct hit cannot leak a restricted record.
        found = find(member_id)
        if found is None or found.restricted:
            abort(404)
        return render_template("record.html", member=found)

    @app.get("/login")
    def login() -> str:
        return render_template("login.html")

    @app.post("/continue")
    def continue_after_notice() -> Response:
        target = request.args.get("next", "/")
        parts = urlsplit(target)
        if parts.scheme or parts.netloc or not target.startswith("/"):
            target = "/"
        return redirect(target)

    return app


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.server")
    parser.add_argument("--variant", choices=sorted(VARIANTS), default="a")
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()
    port = args.port if args.port is not None else DEFAULT_PORTS[args.variant]
    create_app(args.variant).run(host="127.0.0.1", port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
