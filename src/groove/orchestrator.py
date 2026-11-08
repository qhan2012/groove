"""Pure execution loop. All validation and writes belong to Runtime."""

from __future__ import annotations

from groove.contracts import RunOutcome
from groove.runtime import Runtime


def run(run_id: str, runtime: Runtime, *, max_iterations: int = 10) -> RunOutcome:
    if max_iterations < 1:
        raise ValueError("max_iterations must be positive")
    for _ in range(max_iterations):
        try:
            context = runtime.load_context(run_id)
            plan = runtime.invoke_planner(run_id, context)
            result = runtime.invoke_executor(run_id, plan)
            judgment = runtime.invoke_judger(run_id, plan, result)
        except Exception as exc:
            runtime.fail(run_id, f"{type(exc).__name__}: {exc}")
            raise
        if judgment.decision in ("COMPLETE", "STOP"):
            return runtime.outcome(run_id)
    return runtime.limit_reached(run_id)
