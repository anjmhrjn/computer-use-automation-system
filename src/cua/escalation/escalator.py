"""How a paused replay reaches an operator. `Escalator.request` blocks until the
operator answers; the console client does that with one HTTP request the server
holds open (a wait on the operator's action, not a poll and not a sleep)."""

from __future__ import annotations

from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import ValidationError

from cua.schema import InterventionRequest, Resolution


class EscalationError(Exception):
    """The console could not be reached or answered with something that is not a
    resolution. The run fails with its original failure; nothing is retried."""


class Escalator(Protocol):
    def request(self, request: InterventionRequest) -> Resolution: ...


class ConsoleClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    def request(self, request: InterventionRequest) -> Resolution:
        body = request.model_dump_json().encode()
        post = Request(
            f"{self._base_url}/interventions",
            data=body,
            headers={"content-type": "application/json"},
            method="POST",
        )
        self._send(post)
        # Held open by the console until the operator resolves it.
        get = Request(f"{self._base_url}/interventions/{request.request_id}/resolution")
        raw = self._send(get)
        try:
            return Resolution.model_validate_json(raw)
        except ValidationError as exc:
            raise EscalationError(f"console answered with an invalid resolution: {exc}") from exc

    def _send(self, request: Request) -> bytes:
        try:
            with urlopen(request) as response:
                return response.read()
        except HTTPError as exc:
            raise EscalationError(f"console refused {request.full_url}: HTTP {exc.code}") from exc
        except URLError as exc:
            raise EscalationError(f"console unreachable at {request.full_url}: {exc.reason}") from exc
