"""Mock operator console: a stdlib HTTP server holding intervention requests in
memory for its lifetime. A paused replay POSTs its request and then GETs the
resolution; that GET is held open on an Event until an operator submits the form,
so the replay waits on the operator's action and nothing polls.

The console shows only what the request carries, and the request carries only
redacted failure text and parameter names, so there is nothing here to redact."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import parse_qs

from pydantic import ValidationError

from cua.schema import InterventionRequest, Resolution, ResolutionKind

from .render import detail_page, index_page


@dataclass
class Pending:
    request: InterventionRequest
    resolved: threading.Event = field(default_factory=threading.Event)
    resolution: Resolution | None = None


class Console:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, Pending] = {}
        self._order: list[str] = []

    def submit(self, request: InterventionRequest) -> None:
        with self._lock:
            if request.request_id in self._items:
                raise KeyError(request.request_id)
            self._items[request.request_id] = Pending(request)
            self._order.append(request.request_id)

    def get(self, request_id: str) -> Pending | None:
        with self._lock:
            return self._items.get(request_id)

    def all(self) -> list[Pending]:
        with self._lock:
            return [self._items[i] for i in self._order]

    def resolve(self, request_id: str, resolution: Resolution) -> bool:
        with self._lock:
            pending = self._items.get(request_id)
            if pending is None or pending.resolution is not None:
                return False
            pending.resolution = resolution
        pending.resolved.set()
        return True

    def await_resolution(self, request_id: str) -> Resolution | None:
        pending = self.get(request_id)
        if pending is None:
            return None
        pending.resolved.wait()
        assert pending.resolution is not None
        return pending.resolution


def make_handler(console: Console) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

        def do_GET(self) -> None:  # noqa: N802
            parts = self.path.strip("/").split("/")
            if self.path == "/":
                self._html(200, index_page(console.all()))
            elif parts[:1] == ["interventions"] and len(parts) == 1:
                self._json(200, [p.request.model_dump(mode="json") for p in console.all()])
            elif parts[:1] == ["interventions"] and len(parts) == 2:
                self._detail(parts[1])
            elif parts[:1] == ["interventions"] and len(parts) == 3 and parts[2] == "resolution":
                resolution = console.await_resolution(parts[1])
                if resolution is None:
                    self._json(404, {"error": "no such intervention"})
                else:
                    self._json(200, resolution.model_dump(mode="json"))
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            parts = self.path.strip("/").split("/")
            body = self.rfile.read(int(self.headers.get("content-length", "0")))
            if parts == ["interventions"]:
                self._submit(body)
            elif parts[:1] == ["interventions"] and len(parts) == 3 and parts[2] == "resolve":
                self._resolve(parts[1], body)
            else:
                self._json(404, {"error": "not found"})

        def _detail(self, request_id: str) -> None:
            pending = console.get(request_id)
            if pending is None:
                self._html(404, "<p>no such intervention</p>")
            else:
                self._html(200, detail_page(pending))

        def _submit(self, body: bytes) -> None:
            try:
                request = InterventionRequest.model_validate_json(body)
                console.submit(request)
            except ValidationError as exc:
                self._json(400, {"error": str(exc)})
            except KeyError as exc:
                self._json(409, {"error": f"duplicate request_id {exc}"})
            else:
                self._json(201, {"request_id": request.request_id})

        def _resolve(self, request_id: str, body: bytes) -> None:
            form = {k: v[0] for k, v in parse_qs(body.decode()).items()}
            try:
                kind = ResolutionKind(form.get("kind", ""))
                resolution = Resolution(
                    kind=kind,
                    resume_from=form.get("resume_from") if kind is ResolutionKind.retry else None,
                    note=form.get("note", ""),
                )
            except (ValueError, ValidationError) as exc:
                self._html(400, f"<p>invalid resolution: {exc}</p>")
                return
            if not console.resolve(request_id, resolution):
                self._html(409, "<p>already resolved, or no such intervention</p>")
                return
            self.send_response(303)
            self.send_header("location", f"/interventions/{request_id}")
            self.end_headers()

        def _json(self, status: int, payload: object) -> None:
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _html(self, status: int, page: str) -> None:
            data = page.encode()
            self.send_response(status)
            self.send_header("content-type", "text/html; charset=utf-8")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


def serve(host: str, port: int, announce: Callable[[str], None]) -> None:
    server = ThreadingHTTPServer((host, port), make_handler(Console()))
    announce(f"http://{host}:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
