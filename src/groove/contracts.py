"""Typed handoff contracts. No agent owns persistent workflow state."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunContext(StrictModel):
    run_id: str
    goal: str
    acceptance_criteria: list[str]
    iteration: int
    state: dict[str, Any] = Field(default_factory=dict)
    history: list[dict[str, Any]] = Field(default_factory=list)


class ActionPlan(StrictModel):
    objective: str
    action: str
    expected_observation: str
    strategy_ref: str | None = None
    skill_refs: list[str] = Field(default_factory=list)
    verification_checks: list[str] = Field(default_factory=list)


class ExecutionResult(StrictModel):
    status: Literal["SUCCEEDED", "FAILED", "BLOCKED"]
    observations: list[str] = Field(default_factory=list)
    artifact_refs: list[str] = Field(default_factory=list)
    error: str | None = None


class Judgment(StrictModel):
    verification: Literal["PASS", "FAIL", "INCONCLUSIVE"]
    decision: Literal["CONTINUE", "COMPLETE", "STOP"]
    reason: str
    evidence_refs: list[str] = Field(default_factory=list)
    feedback_for_planner: str = ""
    proposed_run_state_delta: dict[str, Any] = Field(default_factory=dict)


class RunOutcome(StrictModel):
    run_id: str
    status: Literal["RUNNING", "COMPLETED", "STOPPED", "LIMIT_REACHED", "ERROR"]
    completed_iterations: int
    state: dict[str, Any]
