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
    parser = argparse.ArgumentParser(prog="groove", description="Groove RSI workflow (mock-only PR1)")
    parser.add_argument("--data-dir", default=".groove", help="Path for SQLite and artifacts")
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="Run a deterministic two-iteration mock workflow")
    demo.add_argument("--max-iterations", type=int, default=5)
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
    context = store.load_context(args.run_id)
    print(json.dumps({"outcome": store.outcome(args.run_id).model_dump(), "history": context.history}, indent=2))
    return 0
