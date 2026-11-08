"""Runnable deterministic demo and run inspection CLI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from groove.agents.mock import MockExecutor, MockJudger, MockPlanner
from groove.orchestrator import run
from groove.runtime import Runtime
from groove.store import SqliteRunStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="groove", description="Groove RSI workflow (mock and LLM backends)")
    parser.add_argument("--data-dir", default=".groove", help="Path for SQLite and artifacts")
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="Run a deterministic two-iteration mock workflow")
    demo.add_argument("--max-iterations", type=int, default=5)
    real = commands.add_parser("run", help="Run with configured Claude / local LLM backends")
    real.add_argument("--config", required=True, help="YAML model/role configuration")
    real.add_argument("--goal", required=True, help="Goal for this run")
    real.add_argument("--criterion", action="append", required=True, help="Acceptance criterion (repeatable)")
    real.add_argument("--max-iterations", type=int, default=None)
    inspect = commands.add_parser("inspect", help="Inspect a previously recorded run")
    inspect.add_argument("run_id")
    args = parser.parse_args(argv)

    store = SqliteRunStore(Path(args.data_dir))
    if args.command == "demo":
        run_id = store.create_run("Demonstrate P-E-J orchestration", ["Two mock iterations with artifact evidence"])
        runtime = Runtime(store, MockPlanner(), MockExecutor(), MockJudger())
        outcome = run(run_id, runtime, max_iterations=args.max_iterations)
        print(json.dumps(outcome.model_dump(), indent=2))
        return 0 if outcome.status == "COMPLETED" else 1
    if args.command == "run":
        from groove.config import load_config
        from groove.factory import make_runtime

        config = load_config(args.config)
        runtime = make_runtime(store, config)
        run_id = store.create_run(args.goal, args.criterion)
        print(f"Run ID: {run_id}", flush=True)
        outcome = run(run_id, runtime, max_iterations=args.max_iterations or config.max_iterations)
        print(json.dumps(outcome.model_dump(), indent=2))
        return 0 if outcome.status == "COMPLETED" else 1
    context = store.load_context(args.run_id)
    print(json.dumps({"outcome": store.outcome(args.run_id).model_dump(), "history": context.history}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
