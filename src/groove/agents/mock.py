"""Deterministic mock roles for testing the workflow, NOT solving user goals."""

from __future__ import annotations

import json

from groove.agents.base import ArtifactWriter
from groove.contracts import ActionPlan, ExecutionResult, Judgment, RunContext


class MockPlanner:
    def run(self, context: RunContext) -> ActionPlan:
        return ActionPlan(
            objective=f"Mock evidence collection, round {context.iteration}",
            action="emit_mock_observation",
            expected_observation="An immutable artifact containing the current mock iteration",
            verification_checks=["artifact_exists"],
        )


class MockExecutor:
    def run(self, plan: ActionPlan, context: RunContext, artifacts: ArtifactWriter) -> ExecutionResult:
        body = json.dumps({"mock": True, "iteration": context.iteration, "goal": context.goal}).encode()
        ref = artifacts.write(f"iteration-{context.iteration}.json", body, "application/json")
        return ExecutionResult(
            status="SUCCEEDED",
            observations=[f"Mock artifact for iteration {context.iteration} written"],
            artifact_refs=[ref],
        )


class MockJudger:
    def run(self, plan: ActionPlan, result: ExecutionResult, context: RunContext) -> Judgment:
        complete = context.iteration >= 2 and result.status == "SUCCEEDED"
        return Judgment(
            verification="PASS" if result.status == "SUCCEEDED" else "FAIL",
            decision="COMPLETE" if complete else "CONTINUE",
            reason="Mock demonstration ends after round two" if complete else "Mock demonstration needs another round",
            evidence_refs=result.artifact_refs,
            feedback_for_planner="Continue mock exercise" if not complete else "",
            proposed_run_state_delta={"mock_last_iteration": context.iteration},
        )
