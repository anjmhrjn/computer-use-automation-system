"""OpenAI behind the `Model` port, Responses API with a strict JSON schema on the
output. The provider is one env var; nothing outside this module imports the SDK."""

from __future__ import annotations

import os

from openai import OpenAI, OpenAIError
from pydantic import BaseModel, ValidationError

from .model import ModelError, Prompt
from .prompt import contract_messages
from .strict_schema import strict_schema
from .turns import Contract, Turn, TurnEnvelope

DEFAULT_MODEL = "gpt-4.1"


class OpenAIModel:
    def __init__(self, model: str = DEFAULT_MODEL, api_key: str | None = None) -> None:
        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise ModelError("OPENAI_API_KEY is not set")
        self._client = OpenAI(api_key=key)
        self._model = model

    def declare(self, goal: str) -> Contract:
        return self._structured(contract_messages(goal), Contract, "contract")

    def next_turn(self, prompt: Prompt) -> Turn:
        messages: list[tuple[str, str]] = [("system", prompt.system)]
        for exchange in prompt.history:
            messages.append(("assistant", exchange.response))
            messages.append(("user", f"Result: {exchange.result}"))
        messages.append(("user", f"Current observation:\n{prompt.observation}"))
        return self._structured(messages, TurnEnvelope, "turn").turn

    def _structured(self, messages: list[tuple[str, str]], schema: type[BaseModel], name: str):
        try:
            response = self._client.responses.create(
                model=self._model,
                input=[{"role": role, "content": content} for role, content in messages],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": name,
                        "strict": True,
                        "schema": strict_schema(schema),
                    }
                },
            )
        except OpenAIError as exc:
            raise ModelError(f"provider call failed: {exc}") from exc
        text = response.output_text
        if not text:
            raise ModelError("provider returned no structured output (refusal or empty)")
        try:
            return schema.model_validate_json(text)
        except ValidationError as exc:
            raise ModelError(f"provider output did not match {name} schema: {exc}") from exc
