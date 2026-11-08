"""OpenAI-compatible chat-completions backend for LM Studio, Ollama, vLLM.

No HTTP requests are made at import/construction time. Tests inject a fake client.
Only registered, explicitly provided tools may be invoked by the model.
"""

from __future__ import annotations

import json
import os
from typing import Any

from groove.backends.base import BackendError, Tool


class LocalBackend:
    supports_callback_tools = True

    def __init__(self, *, model: str, base_url: str, api_key_env: str | None = None,
                 max_tool_rounds: int = 4, timeout_seconds: float = 60.0,
                 client: Any | None = None):
        if not model or not base_url:
            raise ValueError("model and base_url are required")
        if max_tool_rounds < 0:
            raise ValueError("max_tool_rounds must be nonnegative")
        self.model = model
        self.max_tool_rounds = max_tool_rounds
        self.base_url = base_url
        self.api_key_env = api_key_env
        self.timeout_seconds = timeout_seconds
        self.client = client

    def _client(self):
        if self.client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise BackendError("Install with pip install 'groove-rsi[local]'") from exc
            if self.api_key_env:
                key = os.environ.get(self.api_key_env)
                if not key:
                    raise BackendError(f"Missing configured API key environment variable: {self.api_key_env}")
            else:
                key = "not-needed"  # Most local hosts ignore this value.
            self.client = OpenAI(base_url=self.base_url, api_key=key, timeout=self.timeout_seconds)
        return self.client

    def generate(self, *, system_prompt: str, payload: dict[str, Any],
                 response_schema: dict[str, Any], tools: tuple[Tool, ...] = ()) -> dict[str, Any]:
        # Keep the requested output schema in the prompt: not all local hosts support
        # strict response_format=json_schema. Pydantic validates the result later.
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt +
             "\nRespond with ONLY a JSON object matching this schema: " + json.dumps(response_schema)},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        permitted = {tool.name: tool for tool in tools}
        if len(permitted) != len(tools):
            raise BackendError("Duplicate tool names are not allowed")
        for round_id in range(self.max_tool_rounds + 1):
            kwargs: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": 0}
            if tools:
                kwargs["tools"] = [tool.openai_schema() for tool in tools]
                kwargs["tool_choice"] = "auto"
            response = self._client().chat.completions.create(**kwargs)
            if not response.choices:
                raise BackendError("Local model returned no choices")
            message = response.choices[0].message
            calls = message.tool_calls or []
            if calls:
                if round_id >= self.max_tool_rounds:
                    raise BackendError("Local model exceeded max_tool_rounds")
                messages.append(message.model_dump(exclude_none=True))
                for call in calls:
                    name = call.function.name
                    if name not in permitted:
                        raise BackendError(f"Tool is not permitted: {name}")
                    try:
                        args = json.loads(call.function.arguments)
                    except (TypeError, ValueError) as exc:
                        raise BackendError(f"Tool arguments are invalid JSON: {name}") from exc
                    if not isinstance(args, dict):
                        raise BackendError("Tool arguments must be a JSON object")
                    try:
                        output = permitted[name].handler(args)
                    except Exception as exc:
                        raise BackendError(f"Tool execution failed: {name}: {exc}") from exc
                    messages.append({"role": "tool", "tool_call_id": call.id,
                                     "content": json.dumps(output, ensure_ascii=False)})
                continue
            raw = message.content
            if not isinstance(raw, str) or not raw.strip():
                raise BackendError("Local model returned no JSON response")
            try:
                parsed = json.loads(raw)
            except ValueError as exc:
                raise BackendError("Local model did not return valid JSON") from exc
            if not isinstance(parsed, dict):
                raise BackendError("Local model must return a JSON object")
            return parsed
        raise BackendError("Local model exhausted its tool loop")
