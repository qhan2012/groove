# Groove

**A groovy RSI framework.**

Groove is a lightweight, general-purpose framework for **Recursive Self-Improvement (RSI)**, built around a Planner → Executor → Judger loop. This is **PR 1**, the small, runnable foundation; it runs **deterministic mock agents only**, not real LLM tasks.

## Quick start

Requires Python 3.11+.

```bash
python -m pip install -e '.[dev]'
groove demo
groove inspect <run-id>
pytest -q
```

Alternatively, from a source checkout without installing, use `PYTHONPATH=src python -m groove demo` after installing Pydantic. Store files default to `.groove/` (gitignored); override with `--data-dir` before the subcommand.

The mock demo runs twice, writes content-addressed evidence files, and returns `COMPLETED`. **This is a demonstration of workflow machinery, not evidence of solving arbitrary goals.**

## Architecture

- **Planner** builds an ActionPlan from goal, immutable acceptance criteria, history, and run state.
- **Executor** runs the plan and returns an ExecutionResult, optionally writing immutable artifact files through a restricted writer interface.
- **Judger** evaluates the result, returns `CONTINUE`, `COMPLETE`, or `STOP`, and proposes a Run State Delta.
- **Orchestrator** is an execution-only loop. Runtime validates role outputs and writes all events through the Store.
- **Store** persists run metadata, stage events, state deltas, and hash-addressed artifacts using SQLite + local files.
- **Learning** assets (`skills/`, `strategies/`, `knowledge/`) are separate from per-run state. Automatic RSI promotion is not part of PR 1.

A minimal completion gate rejects judgments without a successful execution, `PASS` verification and matching stored artifact evidence. **It checks evidence provenance, not the semantic truth of arbitrary goals.** Task-specific deterministic verification is required before treating an LLM Judger as authoritative in production.

The initial implementation intentionally fails closed on incomplete rounds rather than replaying side-effectful work automatically. Crash resume and retries come later.

## Planned integrations

Future changes will add Claude Agent SDK and an OpenAI-compatible adapter for LM Studio, Ollama and vLLM, configurable by role. The three agent roles and contracts will remain independent of the model backend.

See [architecture](docs/architecture.md) and [implementation proposal](docs/implementation.md).