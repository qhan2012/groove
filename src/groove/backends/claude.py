"""One-shot Claude Agent SDK adapter with a fresh session per role call.

Tools are disabled by default; explicit allowlisted built-ins are the only ones
exposed. The SDK is imported lazily so mock/local use needs no Claude install.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from groove.backends.base import BackendError, Tool


class ClaudeBackend:
    def __init__(self, *, model: str, max_turns: int = 6,
                 allowed_tools: tuple[str, ...] = (), cwd: str | None = None,
                 query_function: Any | None = None, options_factory: Any | None = None):
        if not model or max_turns < 1:
            raise ValueError("model must be set and max_turns must be positive")
        self.model = model
        self.max_turns = max_turns
        self.allowed_tools = list(allowed_tools)
        self.cwd = cwd
        self._query_function = query_function
        self._options_factory = options_factory

    def generate(self, *, system_prompt: str, payload: dict[str, Any],
                 response_schema: dict[str, Any], tools: tuple[Tool, ...] = ()) -> dict[str, Any]:
        # In-process callback tools require SDK MCP registration; don't imply
        # that local OpenAI tool callbacks will work with Claude Agent SDK.
        if tools:
            raise BackendError("Callable tools are not supported by ClaudeBackend yet; use SDK built-ins")
        try:
            from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query
        except ImportError as exc:
            if self._query_function is None or self._options_factory is None:
                raise BackendError("Install with pip install 'groove-rsi[claude]'") from exc
            ResultMessage = None  # test doubles may provide simple objects
            query = self._query_function
            ClaudeAgentOptions = self._options_factory
        if self._query_function is not None:
            query = self._query_function
        if self._options_factory is not None:
            ClaudeAgentOptions = self._options_factory

        kwargs: dict[str, Any] = dict(
            model=self.model, max_turns=self.max_turns,
            system_prompt=system_prompt, tools=self.allowed_tools,
            allowed_tools=self.allowed_tools, permission_mode="dontAsk",
            setting_sources=[], strict_mcp_config=True, mcp_servers={},
            output_format={"type": "json_schema", "schema": response_schema},
            continue_conversation=False,
        )
        if self.cwd:
            workspace = Path(self.cwd).expanduser().resolve()
            if not workspace.is_dir():
                raise BackendError(f"Claude workspace does not exist: {workspace}")
            kwargs["cwd"] = str(workspace)
        options = ClaudeAgentOptions(**kwargs)

        async def once() -> dict[str, Any]:
            found = None
            async for event in query(prompt=json.dumps(payload, ensure_ascii=False), options=options):
                if (ResultMessage is not None and isinstance(event, ResultMessage)) or (
                    ResultMessage is None and hasattr(event, "structured_output")
                ):
                    if getattr(event, "is_error", False):
                        raise BackendError("Claude Agent SDK reported an error")
                    found = getattr(event, "structured_output", None)
            if not isinstance(found, dict):
                raise BackendError("Claude Agent SDK returned no structured JSON output")
            return found

        try:
            return asyncio.run(once())
        except RuntimeError as exc:
            if "asyncio.run() cannot be called" in str(exc):
                raise BackendError("Synchronous Groove CLI cannot run inside an existing event loop") from exc
            raise
