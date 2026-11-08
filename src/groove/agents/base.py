"""Role protocols; real LLM backends can implement these in later PRs."""

from __future__ import annotations

from typing import Protocol

from groove.contracts import ActionPlan, ExecutionResult, Judgment, RunContext


class ArtifactWriter(Protocol):
    def write(self, name: str, content: bytes, media_type: str = "text/plain") -> str: ...


class Planner(Protocol):
    def run(self, context: RunContext) -> ActionPlan: ...


class Executor(Protocol):
    def run(self, plan: ActionPlan, context: RunContext, artifacts: ArtifactWriter) -> ExecutionResult: ...


class Judger(Protocol):
    def run(self, plan: ActionPlan, result: ExecutionResult, context: RunContext) -> Judgment: ...
