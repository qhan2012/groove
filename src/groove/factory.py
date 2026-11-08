"""Connect an Agent Role to a configured backend, without changing the loop."""

from __future__ import annotations

from groove.agents.llm import LLMExecutor, LLMJudger, LLMPlanner
from groove.backends.claude import ClaudeBackend
from groove.backends.local import LocalBackend
from groove.config import GrooveConfig
from groove.runtime import Runtime
from groove.store import SqliteRunStore


def make_runtime(store: SqliteRunStore, config: GrooveConfig) -> Runtime:
    def backend_for(name: str):
        role = getattr(config.agents, name)
        backend = config.backends[role.backend]
        if backend.kind == "claude":
            return ClaudeBackend(model=role.model, max_turns=backend.max_turns,
                                 allowed_tools=tuple(backend.allowed_tools), cwd=backend.workspace)
        return LocalBackend(model=role.model, base_url=backend.base_url or "",
                            api_key_env=backend.api_key_env, max_tool_rounds=backend.max_tool_rounds,
                            timeout_seconds=backend.timeout_seconds)

    return Runtime(store, LLMPlanner(backend_for("planner")),
                   LLMExecutor(backend_for("executor")), LLMJudger(backend_for("judger")))
