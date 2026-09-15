"""Structured run events, one JSON line each. The log cannot exist without a
redactor and redacts the whole serialised line, so there is no field a caller can
add that bypasses invariant 6. A `None` stream discards."""

from __future__ import annotations

import json
import time
from typing import TextIO

from cua.policy import Redactor


class EventLog:
    def __init__(self, stream: TextIO | None, redactor: Redactor) -> None:
        self._stream = stream
        self._redactor = redactor

    @property
    def redactor(self) -> Redactor:
        return self._redactor

    def emit(self, event: str, **fields: object) -> None:
        if self._stream is None:
            return
        line = json.dumps({"ts": time.time(), "event": event, **fields}, default=str)
        self._stream.write(self._redactor.redact(line) + "\n")
