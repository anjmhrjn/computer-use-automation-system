"""Fault injection for MemberServe.

Faults are armed out-of-band through `/__faults` by the test harness or CLI, never
by the automation under test, so nothing fault-specific ever appears in an artifact.
A `before_request` hook fires the armed fault on the next ordinary page request,
which is what makes these session-level (they can hit the search page itself)
rather than record-level.
"""

from __future__ import annotations

import threading
from typing import Literal, get_args

from flask import Blueprint, Flask, Request, Response, jsonify, render_template, request

Kind = Literal["session_expired", "maintenance_notice", "timeout"]
Mode = Literal["once", "until_cleared"]

KINDS: tuple[str, ...] = get_args(Kind)
MODES: tuple[str, ...] = get_args(Mode)


class FaultRegister:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._armed: dict[str, str] = {}
        # One release Event per kind. A hung `timeout` request waits on its Event;
        # clearing the fault sets it. This is the only "wait" in the app and it is a
        # state predicate (fault cleared), not a fixed delay.
        self._release: dict[str, threading.Event] = {k: threading.Event() for k in KINDS}

    def arm(self, kind: str, mode: str) -> None:
        with self._lock:
            self._armed[kind] = mode
            self._release[kind].clear()

    def clear(self, kind: str) -> None:
        with self._lock:
            self._armed.pop(kind, None)
            self._release[kind].set()

    def clear_all(self) -> None:
        for kind in KINDS:
            self.clear(kind)

    def snapshot(self) -> dict[str, str]:
        with self._lock:
            return dict(self._armed)

    def consume(self, kind: str) -> bool:
        """Report whether `kind` is armed; a `once` arming is spent by the call."""
        with self._lock:
            mode = self._armed.get(kind)
            if mode is None:
                return False
            if mode == "once":
                del self._armed[kind]
            return True

    def hold(self, kind: str) -> None:
        """Block the caller while `kind` is armed; `clear()` releases it. Mode is
        irrelevant here -- a hang can only end by being cleared."""
        with self._lock:
            if kind not in self._armed:
                return
            release = self._release[kind]
        release.wait()


def _is_exempt(req: Request) -> bool:
    return req.path.startswith("/__") or req.path.startswith("/static")


def install(app: Flask, register: FaultRegister) -> None:
    control = Blueprint("faults", __name__)

    @control.get("/__faults")
    def list_faults() -> Response:
        return jsonify({"armed": register.snapshot()})

    @control.post("/__faults")
    def arm_fault() -> tuple[Response, int]:
        body = request.get_json(silent=True) or {}
        kind, mode = body.get("kind"), body.get("mode", "once")
        if kind not in KINDS:
            return jsonify({"error": f"unknown kind; expected one of {list(KINDS)}"}), 400
        if mode not in MODES:
            return jsonify({"error": f"unknown mode; expected one of {list(MODES)}"}), 400
        register.arm(kind, mode)
        return jsonify({"armed": register.snapshot()}), 201

    @control.delete("/__faults/<kind>")
    def clear_fault(kind: str) -> tuple[Response, int]:
        if kind not in KINDS:
            return jsonify({"error": f"unknown kind; expected one of {list(KINDS)}"}), 400
        register.clear(kind)
        return jsonify({"armed": register.snapshot()}), 200

    @control.delete("/__faults")
    def clear_faults() -> Response:
        register.clear_all()
        return jsonify({"armed": register.snapshot()})

    app.register_blueprint(control)

    @app.before_request
    def inject_faults() -> Response | tuple[str, int] | None:
        if _is_exempt(request):
            return None
        # Order matters: a hung request, once released, still goes on to face the
        # interstitials below. Flask's dev server is threaded by default, so the
        # DELETE that releases a hang gets through while this request is blocked.
        register.hold("timeout")
        if register.consume("session_expired"):
            return render_template("session_expired.html"), 401
        if register.consume("maintenance_notice"):
            return render_template("maintenance.html", next=request.full_path.rstrip("?")), 200
        return None
