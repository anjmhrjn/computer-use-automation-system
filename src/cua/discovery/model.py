"""The LLM provider port. Two calls: declare the contract from the goal text, then
one structured turn per observation. `Prompt` is already redacted by the loop; an
adapter only flattens it into its provider's message format. This is the `Model`
port; the artifact types in `cua/schema/` are a different thing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from cua.schema import TargetDescriptor

from .turns import Contract, Turn


@dataclass(frozen=True)
class Exchange:
    """One earlier turn as the model sees it again: what it said, what happened."""

    response: str
    result: str


@dataclass(frozen=True)
class Prompt:
    """`locate` resolves a recorded descriptor to a node id in the observation this
    prompt was rendered from, or raises `ResolutionError`. It exists for
    `TranscriptModel`, whose recorded node ids are meaningless in a new run; a
    provider adapter has no use for it and no data reaches it through it."""

    system: str
    history: tuple[Exchange, ...]
    location: str
    observation: str
    locate: Callable[[TargetDescriptor], str]


class ModelError(Exception):
    """The provider did not return a usable structured turn."""


class Model(Protocol):
    def declare(self, goal: str) -> Contract: ...

    def next_turn(self, prompt: Prompt) -> Turn: ...
