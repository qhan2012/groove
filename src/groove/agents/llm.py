"""Provider-independent implementations of the three *stateless* agent roles."""

from __future__ import annotations

from importlib.resources import files
from typing import Any

from pydantic import Field

from groove.agents.base import ArtifactWriter
from groove.backends.base import AgentBackend, BackendError, Tool
from groove.contracts import (
    ActionPlan, ExecutionResult, Judgment, RunContext, StrictModel,
)


class InlineArtifact(StrictModel):
    name: str
    content: str
    media_type: str = "text/plain"


class GeneratedExecution(StrictModel):
    status: str
    observations: list[str] = Field(default_factory=list)
    error: str | None = None
    artifact_refs: list[str] = Field(default_factory=list)
    artifacts: list[InlineArtifact] = Field(default_factory=list)


def role_prompt(role: str) -> str:
    if role not in {"planner", "executor", "judger"}:
        raise ValueError(f"Unknown role: {role}")
    return files("groove").joinpath("role_prompts", f"{role}.md").read_text(encoding="utf-8")


def _json_payload(context: RunContext, **extra: Any) -> dict[str, Any]:
    return {"run_context": context.model_dump(mode="json"), **extra}


class LLMPlanner:
    def __init__(self, backend: AgentBackend):
        self.backend = backend

    def run(self, context: RunContext) -> ActionPlan:
        data = self.backend.generate(system_prompt=role_prompt("planner"),
                                     payload=_json_payload(context),
                                     response_schema=ActionPlan.model_json_schema())
        return ActionPlan.model_validate(data)


class LLMExecutor:
    def __init__(self, backend: AgentBackend, *, max_artifact_bytes: int = 262_144):
        self.backend = backend
        self.max_artifact_bytes = max_artifact_bytes

    def run(self, plan: ActionPlan, context: RunContext, artifacts: ArtifactWriter) -> ExecutionResult:
        issued: list[str] = []
        consumed_bytes = 0

        def publish(args: dict[str, Any]) -> dict[str, str]:
            nonlocal consumed_bytes
            artifact = InlineArtifact.model_validate(args)
            body = artifact.content.encode("utf-8")
            consumed_bytes += len(body)
            if consumed_bytes > self.max_artifact_bytes:
                raise BackendError("Artifact size budget exceeded")
            ref = artifacts.write(artifact.name, body, artifact.media_type)
            issued.append(ref)
            return {"artifact_ref": ref}

        tool = Tool(
            name="publish_artifact",
            description="Persist a UTF-8 text artifact and return its immutable reference.",
            parameters={"type": "object", "properties": {
                "name": {"type": "string"}, "content": {"type": "string"},
                "media_type": {"type": "string"}},
                "required": ["name", "content"], "additionalProperties": False},
            handler=publish,
        )
        tools = (tool,) if getattr(self.backend, "supports_callback_tools", False) else ()
        raw = self.backend.generate(
            system_prompt=role_prompt("executor"),
            payload=_json_payload(context, plan=plan.model_dump(mode="json")),
            response_schema=GeneratedExecution.model_json_schema(), tools=tools,
        )
        generated = GeneratedExecution.model_validate(raw)
        if any(ref not in issued for ref in generated.artifact_refs):
            raise BackendError("Agent referenced an artifact it did not publish")
        for inline in generated.artifacts:
            publish(inline.model_dump())
        # Never accept model-invented artifact references. Normalize to actual writes.
        return ExecutionResult(status=generated.status, observations=generated.observations,
                               artifact_refs=list(dict.fromkeys(issued)), error=generated.error)


class LLMJudger:
    def __init__(self, backend: AgentBackend):
        self.backend = backend

    def run(self, plan: ActionPlan, result: ExecutionResult, context: RunContext) -> Judgment:
        raw = self.backend.generate(
            system_prompt=role_prompt("judger"),
            payload=_json_payload(context, plan=plan.model_dump(mode="json"),
                                  result=result.model_dump(mode="json")),
            response_schema=Judgment.model_json_schema(),
        )
        return Judgment.model_validate(raw)
