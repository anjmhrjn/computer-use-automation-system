"""Strips what must never leave the process (invariants 6 and 7). Two layers: the
runtime values of sensitive inputs, which are known exactly and replaced by the
parameter's name, and a fixed set of shapes that look like PII or credentials
wherever they came from."""

from __future__ import annotations

import re
from dataclasses import dataclass

from cua.schema import Capability, Sensitivity

SENSITIVE = frozenset({Sensitivity.pii, Sensitivity.secret})

# (label, pattern). Tokens first so an email inside a bearer token is one hit.
PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("token", re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{8,}")),
    ("token", re.compile(r"(?i)\bapi[_-]?key\s*[=:]\s*\S+")),
    ("token", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("phone", re.compile(r"(?<!\w)(?:\+1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?!\w)")),
)


@dataclass(frozen=True)
class Redactor:
    """`values` maps parameter name to its runtime value, sensitive ones only."""

    values: tuple[tuple[str, str], ...]

    @classmethod
    def for_run(cls, capability: Capability, params: dict[str, str]) -> "Redactor":
        pairs = [
            (spec.name, params[spec.name])
            for spec in capability.inputs
            if spec.sensitivity in SENSITIVE and params.get(spec.name)
        ]
        # Longest value first so a value that contains another is replaced whole.
        pairs.sort(key=lambda pair: len(pair[1]), reverse=True)
        return cls(tuple(pairs))

    def redact(self, text: str) -> str:
        for name, value in self.values:
            # Token-bounded: a value of "1" must not shred every digit in the text.
            text = re.sub(rf"(?<!\w){re.escape(value)}(?!\w)", f"<param:{name}>", text)
        for label, pattern in PATTERNS:
            text = pattern.sub(f"<redacted:{label}>", text)
        return text
