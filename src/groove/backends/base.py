"""A small provider-independent boundary for one *stateless* agent invocation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol


class BackendError(RuntimeError):
    """An LLM or tool invocation could not return a valid structured response."""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any]], dict[str, Any]]

    def openai_schema(self) -> dict[str, Any]:
        return {"type": "function", "function": {"name": self.name,
                "description": self.description, "parameters": self.parameters}}


class AgentBackend(Protocol):
    def generate(self, *, system_prompt: str, payload: dict[str, Any],
                 response_schema: dict[str, Any], tools: tuple[Tool, ...] = ()) -> dict[str, Any]: ...
