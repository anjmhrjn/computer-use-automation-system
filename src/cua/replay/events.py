"""Structured run events, one JSON line each. The log cannot exist without a
redactor and redacts the whole serialised line, so there is no field a caller can
add that bypasses invariant 6. Every stream gets the same redacted line; no
streams discards."""

from __future__ import annotations

import json
import time
from typing import TextIO

from cua.policy import Redactor


class EventLog:
    def __init__(self, streams: tuple[TextIO, ...], redactor: Redactor) -> None:
        self._streams = streams
        self._redactor = redactor

    @property
    def redactor(self) -> Redactor:
        return self._redactor

    def emit(self, event: str, **fields: object) -> None:
        if not self._streams:
            return
        line = json.dumps({"ts": time.time(), "event": event, **fields}, default=str)
        redacted = self._redactor.redact(line) + "\n"
        for stream in self._streams:
            stream.write(redacted)
