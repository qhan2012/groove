# Groove

**A groovy RSI framework.**

Groove is a lightweight, general-purpose framework for **Recursive Self-Improvement (RSI)**, built around a Planner → Executor → Judger loop. The project supports both a deterministic mock demo and configurable **Claude Agent SDK / OpenAI-compatible LLM backends**. External provider integrations have mocked tests; live model connectivity is environment-dependent and has not been verified here.

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

## Real LLM backends (PR 2)

Groove keeps the same Planner / Executor / Judger contracts for each backend. A fresh agent invocation receives the role prompt and an explicit Run Context; hidden session history is not used.

### Local LLM: LM Studio, Ollama, or vLLM

```bash
python -m pip install -e '.[local,dev]'
# Start your host and load a model before running. Then edit the model IDs and base URL:
cp configs/local.example.yaml configs/local.yaml
groove run --config configs/local.yaml --goal 'Write a concise test report' \
  --criterion 'A report artifact is persisted' --max-iterations 3
```

Known local defaults: LM Studio `http://localhost:1234/v1`, Ollama `http://localhost:11434/v1`, vLLM `http://localhost:8000/v1`. The model must support valid JSON output and (for artifact tools) OpenAI-compatible tool calls. The Local Backend allows only the registered `publish_artifact` callback; it **does not** run arbitrary Bash commands or edit repository files. The Executor may also submit small UTF-8 artifacts in its structured response. This is a *minimal agent integration*, not a complete coding-agent runtime.

### Claude Agent SDK or hybrid

```bash
python -m pip install -e '.[models,dev]'
cp configs/hybrid.example.yaml configs/hybrid.yaml
# Set up Claude SDK authentication, start the configured local inference host,
# and edit model IDs and any explicitly approved workspace/tool settings.
groove run --config configs/hybrid.yaml --goal 'Summarize a debugging finding' \
  --criterion 'A report artifact contains evidence' --max-iterations 3
```

The SDK runs with a fresh session per call. Default Claude built-in tool access is disabled. If you intentionally configure tools (for example Read/Grep/Glob), also specify a `workspace`, and review permissions before executing; `permission_mode=dontAsk` denies tools not explicitly allowed. Claude's built-in tools are **not** the same as local registered Python callbacks; the current Claude Executor can persist a small inline artifact in its structured JSON response.

The Judger's `COMPLETE` decision still requires persisted artifact evidence and `PASS`, but this is **provenance validation, not a proof of semantic correctness**. Real-world tasks should supply domain-specific verification checks. Runtime records are stored in `.groove/` and should be treated as potentially sensitive; do not commit them.

### Scope

- Implemented: swappable Claude / local backends, per-role selection, bounded local callback-tool loop, typed outputs, artifact persistence, CLI configuration, mock-based unit tests.
- Not yet implemented: external model E2E validation, unrestricted local coding tools, crash-safe replay of partially completed tool actions, and cross-run RSI candidate learning/promotion.


See [architecture](docs/architecture.md) and [implementation proposal](docs/implementation.md).
