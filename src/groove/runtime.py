"""Validated role invocations and persistence. Orchestrator delegates all mutations here."""

from __future__ import annotations

from dataclasses import dataclass

from groove.agents.base import Executor, Judger, Planner
from groove.contracts import ActionPlan, ExecutionResult, Judgment, RunContext, RunOutcome
from groove.store import SqliteRunStore, StoreError


@dataclass
class _Writer:
    store: SqliteRunStore
    run_id: str

    def write(self, name: str, content: bytes, media_type: str = "text/plain") -> str:
        return self.store.write_artifact(self.run_id, name, content, media_type)


class Runtime:
    def __init__(self, store: SqliteRunStore, planner: Planner, executor: Executor, judger: Judger):
        self.store, self.planner, self.executor, self.judger = store, planner, executor, judger

    def load_context(self, run_id: str) -> RunContext:
        context = self.store.load_context(run_id)
        # PR1 fails closed on a partially completed round: no implicit replay of side effects.
        if context.history and context.history[-1]["stage"] != "judgment":
            raise StoreError("Incomplete previous iteration; inspect before replaying side effects")
        if self.store.outcome(run_id).status != "RUNNING":
            raise StoreError("Cannot resume a terminal run")
        return context

    def invoke_planner(self, run_id: str, context: RunContext) -> ActionPlan:
        plan = ActionPlan.model_validate(self.planner.run(context))
        self.store.append_event(run_id, context.iteration, "plan", plan.model_dump())
        return plan

    def invoke_executor(self, run_id: str, plan: ActionPlan) -> ExecutionResult:
        context = self.store.load_context(run_id)
        result = ExecutionResult.model_validate(self.executor.run(plan, context, _Writer(self.store, run_id)))
        if any(not self.store.has_artifact(run_id, ref) for ref in result.artifact_refs):
            raise StoreError("Executor returned an unknown or corrupted artifact reference")
        self.store.append_event(run_id, context.iteration, "execution", result.model_dump())
        return result

    def invoke_judger(self, run_id: str, plan: ActionPlan, result: ExecutionResult) -> Judgment:
        context = self.store.load_context(run_id)
        judgment = Judgment.model_validate(self.judger.run(plan, result, context))
        if judgment.decision == "COMPLETE":
            if result.status != "SUCCEEDED" or judgment.verification != "PASS":
                raise StoreError("COMPLETE requires successful execution and PASS verification")
            if not judgment.evidence_refs or not set(judgment.evidence_refs).issubset(result.artifact_refs):
                raise StoreError("COMPLETE requires evidence produced by this execution")
            if not all(self.store.has_artifact(run_id, ref) for ref in judgment.evidence_refs):
                raise StoreError("COMPLETE evidence is missing or corrupted")
        status = {"CONTINUE": "RUNNING", "COMPLETE": "COMPLETED", "STOP": "STOPPED"}[judgment.decision]
        self.store.commit_judgment(run_id, context.iteration, judgment.model_dump(),
                                   judgment.proposed_run_state_delta, status)
        return judgment

    def fail(self, run_id: str, reason: str) -> None:
        context = self.store.load_context(run_id)
        try:
            self.store.append_event(run_id, context.iteration, "error", {"reason": reason})
        finally:
            self.store.set_status(run_id, "ERROR")

    def limit_reached(self, run_id: str) -> RunOutcome:
        self.store.set_status(run_id, "LIMIT_REACHED")
        return self.store.outcome(run_id)

    def outcome(self, run_id: str) -> RunOutcome:
        return self.store.outcome(run_id)
