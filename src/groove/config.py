"""Explicit backend selection from a YAML configuration file."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from groove.contracts import StrictModel


class BackendSpec(StrictModel):
    kind: Literal["openai_compatible", "claude"]
    base_url: str | None = None
    api_key_env: str | None = None
    timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    max_tool_rounds: int = Field(default=4, ge=0, le=30)
    max_turns: int = Field(default=6, ge=1, le=40)
    allowed_tools: list[str] = Field(default_factory=list)
    workspace: str | None = None

    @model_validator(mode="after")
    def require_local_url(self):
        if self.kind == "openai_compatible" and not self.base_url:
            raise ValueError("base_url required for openai_compatible backend")
        if self.kind == "claude" and self.allowed_tools and not self.workspace:
            raise ValueError("Claude built-in tools require an explicit workspace")
        if self.kind == "openai_compatible" and self.allowed_tools:
            raise ValueError("allowed_tools refers to Claude built-in tools, not local callbacks")
        return self


class AgentSpec(StrictModel):
    backend: str
    model: str


class RoleSpecs(StrictModel):
    planner: AgentSpec
    executor: AgentSpec
    judger: AgentSpec


class GrooveConfig(StrictModel):
    backends: dict[str, BackendSpec]
    agents: RoleSpecs
    max_iterations: int = Field(default=10, ge=1, le=1000)

    @model_validator(mode="after")
    def references_exist(self):
        for role in (self.agents.planner, self.agents.executor, self.agents.judger):
            if role.backend not in self.backends:
                raise ValueError(f"Undefined backend: {role.backend}")
        return self


def load_config(path: str | Path) -> GrooveConfig:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("Install with pip install 'groove-rsi[models]'") from exc
    file = Path(path).expanduser().resolve()
    raw = yaml.safe_load(file.read_text(encoding="utf-8"))
    config = GrooveConfig.model_validate(raw)
    # Workspace paths are explicitly scoped relative to the config file.
    for backend in config.backends.values():
        if backend.workspace:
            backend.workspace = str((file.parent / backend.workspace).resolve())
    return config
