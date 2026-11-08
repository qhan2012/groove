# Agent Workflow + RSI — Source Code Scaffold Proposal

**Version:** Proposal P1 (2026-11-07) — Claude Agent SDK + local LLM backend selection  
**Based on:** `docs/architecture.md` V1.3  
**Status:** For review; this is a source-layout and implementation proposal, **not** a completed codebase.

## 1. Goals and non-goals

Build a small, general-purpose Python framework that can run **Planner → Executor → Judger** for debugging, coding, profiling, research, or experiments, then extend it to RSI without changing the core loop.

**Invariants**

- Three stateless logical Agents with fixed, versioned Role Prompts; each invocation uses an explicitly selected backend.
- The **Orchestrator is only a loop**. It invokes steps and observes `CONTINUE / COMPLETE / STOP`; it performs no domain reasoning and does not edit business state.
- **Runtime/Store APIs**, not Agents or Orchestrator business logic, validate typed outputs, persist events/artifacts, and apply allowed Run State deltas.
- **Run Context** (goal, criteria, run state, history, evidence refs) is separate from versioned **Learning Assets** (Knowledge Base, Strategies, Skills). No fourth “Dynamic Context” resource.
- RSI may propose changes only to **KB, Strategies, Skills**. Prompts, Orchestrator code, Goal and acceptance criteria are outside automatic RSI scope.
- **Agent role != model backend.** Claude Agent SDK and a local OpenAI-compatible Agent Runtime are selectable per role, without changing the outer orchestration loop; tool availability and schema support require capability checks.
- Judger combines step verification and goal-level decision; it may invoke objective verification tools. A `COMPLETE` judgment requires evidence and must pass fixed system checks.

**Non-goals for initial scaffold:** multi-agent concurrency, distributed queues, vector DB, an autonomous code-rewriting engine, agent-directed changes to verifier/acceptance criteria, and arbitrary unrestricted shell access.

## 2. Proposed repository tree

```text
agent-rsi/
├── pyproject.toml
├── README.md
├── .env.example
├── src/agent_rsi/
│   ├── cli.py                   # run / inspect / resume commands
│   ├── orchestrator.py          # deliberately tiny P→E→J loop
│   ├── runtime.py               # typed invocation, stage recording, system gates
│   ├── contracts.py             # Pydantic inputs / outputs / decisions
│   ├── context.py               # build Run Context and asset references
│   ├── config.py                # validated backend/role configuration loader
│   ├── backends/
│   │   ├── base.py              # AgentBackend Protocol + AgentRequest/Response
│   │   ├── claude_sdk.py        # Claude Agent SDK; fresh invocation/session
│   │   ├── openai_compatible.py # LM Studio/Ollama/vLLM inference client
│   │   ├── local_agent_loop.py  # bounded tool-call/observe loop for local models
│   │   └── factory.py           # selects backend per role + capability checks
│   ├── store.py                 # SQLite run events/state; file artifact storage
│   ├── assets.py                # lookup/version pinning for KB/Strategies/Skills
│   ├── tools.py                 # allowlisted execution + verification tools
│   └── agents/
│       ├── planner.py
│       ├── executor.py
│       └── judger.py
├── config/
│   └── agents.yaml              # role→backend→model + host endpoints
├── prompts/                     # fixed/versioned; not RSI-mutable
│   ├── planner.md
│   ├── executor.md
│   └── judger.md
├── learning/                    # ONLY automatic RSI-mutable directory
│   ├── knowledge/cases/
│   │   └── example_nan_case.md
│   ├── strategies/
│   │   ├── general/hypothesis_driven.md
│   │   └── debugging/reproduction_amplification.md
│   ├── skills/
│   │   └── capture_trace/
│   │       ├── SKILL.md
│   │       └── run.py
│   └── candidates/             # unapproved learning deltas
├── tests/
│   ├── test_orchestrator.py
│   ├── test_contracts.py
│   ├── test_store_resume.py
│   ├── test_asset_versions.py
│   └── test_judger_gate.py
├── evals/                       # fixed hold-out benchmark cases
└── runs/                        # generated; gitignored
    └── <run_id>/artifacts/
```

**Keep the first implementation simple:** `sqlite3` (stdlib) for event/state metadata; content-addressed local files for artifacts; Git versioning for `learning/`; Pydantic for validation; pytest for tests. **Two backend adapters** share one contract: Claude Agent SDK and OpenAI-compatible local inference plus a small local tool loop. No microservices.

## 3. Responsibility and API boundaries

| Component | Main API | Owns / does | Must not do |
|---|---|---|---|
| Orchestrator | `run(run_id, runtime)` | Sequential call/loop, terminate on explicit decision or limit | Domain reasoning, edit State/KB/Strategy/Skill |
| Runtime | `planner()`, `executor()`, `judger()` | Build invocation, enforce schemas/budgets, append each stage, call Store | Choose investigation strategy on its own |
| Planner | `run(PlannerInput) -> ActionPlan` | Read Run Context, look up KB/Strategies, choose Skill(s), propose action | Execute side effects, write State |
| Executor | `run(ExecutorInput) -> ExecutionResult` | Load allowed Skill, invoke approved tools, return evidence refs | Redefine Goal/criteria, write Run State |
| Judger | `run(JudgerInput) -> Judgment` | Verify this step, decide Goal completion, propose Run State Delta | Change criteria, promote learning assets, write State directly |
| Store | `append_event`, `commit_judgment`, `load_context`, `save_artifact` | Single authoritative writer to run records, safe state merge and durable artifacts | Domain decisions |
| Asset Registry | `search(kind, query)`, `get(ref)`, `approved_snapshot()` | Read/version-pinned KB/Strategies/Skills | Dynamically mutate assets during a Run |
| Backend Factory | `get_backend(role, config)` | Resolve Claude/local backend, capabilities and model for each role | Decide investigation logic, silently switch providers |
| Agent Backend | `run(AgentRequest) -> AgentResponse` | Drive **one role invocation**, including inner tool loop | Persist global state, decide the outer P→E→J flow |

**Execution ownership:** The Orchestrator invokes `runtime.invoke_planner/execute/judge`. The Runtime wrapper immediately validates and saves each stage. There is **no separate Recorder Agent or service**. The Planner can use read-only asset lookups; the Executor can use allowlisted execution tools; the Judger can use allowlisted verification tools.

## 4. Minimal typed contracts

Illustrative Pydantic API (fields can be refined without changing the architecture):

```python
from typing import Any, Literal
from pydantic import BaseModel, Field

class ActionPlan(BaseModel):
    objective: str
    strategy_ref: str | None = None
    skill_refs: list[str] = Field(default_factory=list)
    action: str
    expected_observation: str
    verification_checks: list[str] = Field(default_factory=list)

class ExecutionResult(BaseModel):
    status: Literal["SUCCEEDED", "FAILED", "BLOCKED"]
    observations: list[str] = Field(default_factory=list)
    artifact_refs: list[str] = Field(default_factory=list)
    error: str | None = None

class Judgment(BaseModel):
    verification: Literal["PASS", "FAIL", "INCONCLUSIVE"]
    decision: Literal["CONTINUE", "COMPLETE", "STOP"]
    reason: str
    evidence_refs: list[str] = Field(default_factory=list)
    feedback_for_planner: str = ""
    proposed_run_state_delta: dict[str, Any] = Field(default_factory=dict)
```

Additional inputs are wrappers (not new resource categories): `PlannerInput = Run Context + asset discovery access`; `ExecutorInput = ActionPlan + applicable Run Context + pinned Skill refs`; `JudgerInput = immutable Goal/criteria + Plan + Result + evidence/verification tools`.

Important rules: `ExecutionResult.status == SUCCEEDED` does **not** mean Goal complete. `Judgment.decision == COMPLETE` is accepted only if the system validates its evidence against immutable criteria, not merely because the LLM says so. `proposed_run_state_delta` can update only approved Run fields via Store; it is never an RSI Learning Delta.

## 5. Pure Orchestrator — intended shape

```python
def run(run_id: str, runtime, max_iterations: int = 10):
    for _ in range(max_iterations):
        context = runtime.load_context(run_id)
        plan = runtime.invoke_planner(run_id, context)          # validates + records
        result = runtime.invoke_executor(run_id, plan)          # persists artifacts + result
        judgment = runtime.invoke_judger(run_id, plan, result)  # validates + commits run delta

        if judgment.decision in {"COMPLETE", "STOP"}:
            return runtime.outcome(run_id)

    return runtime.limit_reached(run_id)
```

`runtime` owns logging, schema/policy gates, tool sandbox boundaries, retries, timeouts, and durable stage records. The Orchestrator does not merge or reinterpret State. When a stage fails, Runtime records the failure and returns/raises a typed failure; the top-level runner can stop or retry within fixed limits.

## 6. How KB, Strategies, and Skills enter the call

1. **Planner** gets Goal + latest Run Context and read-only `AssetRegistry.search/get` tools. It retrieves KB cases and Strategy guidance; it sees Skill metadata (names, descriptions, input schemas). It returns an Action Plan referencing exact asset IDs/versions.
2. **Executor** receives the Plan, looks up the selected, **version-pinned** Skill, invokes allowed tools/scripts, and stores immutable artifacts. It may read KB if needed.
3. **Judger** receives the original criteria, the Plan/Result, evidence refs, and objective verification tools. It may read KB as supporting context, but must not overwrite the criteria.
4. **Runtime** records which exact Prompt/Skill/Strategy/KB versions were used on each invocation. A Run pins an approved asset snapshot at start; new learning is available to **future Runs**, not silently injected into an existing one.

Strategy hierarchy is **logical** (General → Domain → Tactical), implemented via tags and references, not nested Planner Agents or a new Orchestrator stage.

## 7. Agent Backend abstraction and local LLM host support (new in P1)

### 7.1 Two different loops, one stable role contract

```text
                        Outer workflow loop (deterministic Python)
                     Planner → Executor → Judger → continue/stop
                           |          |          |
                   Backend Factory chooses one per role
                           |          |          |
                      AgentBackend.run(AgentRequest)
                          /                      \
               ClaudeSdkBackend           LocalAgentBackend
               Claude Agent SDK           bounded inner agent loop
                 (SDK tools)                 (tools + observations)
                          \                      /
                         Shared tool policies / contracts

                   LocalAgentBackend → OpenAI-compatible HTTP
                                  LM Studio / Ollama / vLLM
```

**Key distinction:** LM Studio, Ollama and vLLM primarily provide *model inference*. A local inference endpoint is **not** automatically an agent that can execute tools. Our `LocalAgentBackend` owns the **inner** tool-call/observation loop, schema parsing and turn/tool budgets. Claude Agent SDK provides an agentic runtime on the Claude path. The **outer** Orchestrator remains unchanged.

The preferred V1 path uses a common OpenAI-compatible `/v1/chat/completions` API for local hosts; this is not a claim of identical feature support. Some installations, such as LM Studio, also expose Anthropic-compatible Messages, which **may** permit direct SDK integration after an explicit compatibility test. That path is optional, not the default architecture assumption.

### 7.2 Minimal backend contract

```python
from typing import Any, Literal, Protocol
from pydantic import BaseModel, Field

Role = Literal["planner", "executor", "judger"]

class AgentRequest(BaseModel):
    run_id: str
    invocation_id: str
    role: Role
    role_prompt: str                 # fixed, versioned
    run_context: dict[str, Any]      # goal, state, history, artifact refs
    asset_refs: list[str] = Field(default_factory=list)
    expected_output_schema: str      # e.g. ActionPlan / ExecutionResult / Judgment
    allowed_tool_names: list[str] = Field(default_factory=list)
    max_tool_turns: int = 8

class AgentResponse(BaseModel):
    payload: dict[str, Any]          # parsed+schema-validated by Runtime
    model_id: str
    backend_id: str
    tool_call_refs: list[str] = Field(default_factory=list)

class AgentBackend(Protocol):
    async def run(self, request: AgentRequest) -> AgentResponse: ...
```

`AgentBackend` wraps an **entire logical role invocation**, not a single raw text-generation request. The `Role Prompt`, `Run Context`, and **relevant** KB/Strategies/Skills references remain the only three classes of agent input; backend configuration is execution metadata, **not** a fourth Learning Asset. The Runtime owns Pydantic validation against the actual typed contract; a model-generated schema-conforming output is not automatically correct.

### 7.3 Backend implementations and responsibility split

| Module | Responsibility | Notes |
|---|---|---|
| `backends/claude_sdk.py` | Wrap Claude Agent SDK with an independent, fresh session/invocation; SDK tool loop | Explicit role-specific permissions and tool config; no hidden cross-call session dependency |
| `backends/openai_compatible.py` | Async inference client, timeout, model ID, base URL, authentication | No tool execution in this HTTP client |
| `backends/local_agent_loop.py` | Inspect tool calls, validate arguments, run approved tools, return observations, repeat until structured result or budget | Tool execution happens in controlled **worker runtime**, not inside model server |
| `backends/factory.py` | Map role → backend/model/config; perform capability checks | Fail fast on unsupported features; never silently fallback |
| `runtime.py` / `tools.py` | Enforcement, version pinning, tool allowlists, workspace isolation, stage persistence | Common safety envelope for both backends |

**Local tool loop pseudocode (implementation sketch, not full source):**

```python
async def run_local_role(request, model_client, tool_runtime):
    messages = build_messages(request)
    for _ in range(request.max_tool_turns):
        reply = await model_client.complete(
            messages=messages,
            tools=tool_runtime.schemas(request.allowed_tool_names),
        )
        if reply.tool_calls:
            messages.append(reply.assistant_message)
            for call in reply.tool_calls:
                arguments = tool_runtime.validate(call)  # schema + allowlist
                observation = await tool_runtime.execute(call.name, arguments)
                messages.append(tool_observation(call.id, observation))
            continue
        return parse_typed_result(reply, request.expected_output_schema)
    raise ToolTurnLimitExceeded(request.invocation_id)
```

`build_messages`, `parse_typed_result` and `tool_observation` are application helpers, **not** SDK functions. Add bounded retries for invalid JSON, cancellation, timeout, and error event recording. The tool runtime should deny side effects for Planner, restrict Executor to approved workspace tools, and grant Judger read/test tools only. Store the tool call + observation artifacts for replay. Tool-call syntax and structured-output guarantees are **model- and host-dependent**.

### 7.4 Role-based backend selection (hybrid example)

`config/agents.yaml`:

```yaml
backends:
  claude:
    kind: claude_agent_sdk
    # SDK credentials are read from the environment
  local_lmstudio:
    kind: openai_compatible
    base_url: http://localhost:1234/v1
    api_key_env: LOCAL_LLM_API_KEY
  local_ollama:
    kind: openai_compatible
    base_url: http://localhost:11434/v1
    api_key_env: LOCAL_LLM_API_KEY
  local_vllm:
    kind: openai_compatible
    base_url: http://localhost:8000/v1
    api_key_env: LOCAL_LLM_API_KEY

agents:
  planner:
    backend: claude
    model: <claude-model-id>
  executor:
    backend: claude
    model: <claude-model-id>
  judger:
    backend: local_lmstudio
    model: <installed-local-model-id>
```

Angle-bracket values above are placeholders. Configure actual available model IDs; do not assume a model advertised by one server is available on the others. The ports are common defaults, not a mandatory deployment choice. For services on **another machine** or from WSL2 to a Windows-hosted service, replace `localhost` with a *reachable, secured host endpoint*; verify networking before connecting.

The **same role** can be switched to local just by changing `backend` and `model`, *if* the new backend passes its capability tests. This is a deploy/configuration choice, **not** an RSI Learning Delta; pin each Run's backend/model/config versions to avoid silent drift and preserve comparisons.

### 7.5 Host compatibility and security checklist

| Host | Typical OpenAI-compatible URL | V1 expectation |
|---|---|---|
| LM Studio | `http://localhost:1234/v1` | Chat completions; tools/JSON subject to loaded model capabilities; optional separate Anthropic-compatible mode |
| Ollama | `http://localhost:11434/v1` | Chat completions; tool calls depend on loaded model and API implementation |
| vLLM | `http://localhost:8000/v1` | Chat completions; tool support depends on served model, chat template and server configuration |

**Required preflight** before assigning a role to a new host/model: connectivity + model listing; basic completion; exact ActionPlan/ExecutionResult/Judgment JSON parsing; tool-call round trip; invalid arguments rejection; timeout/streaming behavior if enabled; memory/context-length fit; clean independent sessions. Some servers support responses/streaming with different behavior; V1 standardizes on **Chat Completions** to reduce moving parts.

**Safety**: do not expose a local inference server publicly without network restrictions/auth; keep API keys out of Git; do not allow model-produced arbitrary shell commands beyond the executor's approved sandbox; treat retrieved files and model output as untrusted. For vLLM especially, authentication on API routes alone may not secure every exposed route; prefer a private network/reverse proxy.

**Backend changes are evaluated separately from RSI learning.** With a frozen Goal, criteria, dataset, role prompts, learning assets and tool policy, compare Claude-only, Hybrid and local-only configurations on completion accuracy, false COMPLETE rate, runtime, tool-call validity and cost. No automatic fallback in benchmark runs (unless fallback is itself the explicit test condition).

### 7.6 Documentation references (implementation validation)

- LM Studio, OpenAI compatibility: https://lmstudio.ai/docs/developer/openai-compat
- LM Studio, Anthropic compatibility: https://lmstudio.ai/docs/developer/anthropic-compat
- Ollama, OpenAI compatibility: https://github.com/ollama/ollama/blob/main/docs/api/openai-compatibility.mdx
- vLLM, OpenAI-compatible server: https://docs.vllm.ai/en/stable/serving/openai_compatible_server/

## 8. Run persistence vs RSI persistence

```text
Run data (SQLite + runs/<run_id>/artifacts/):
  goal / immutable acceptance-criteria ref
  stage events: plan, execution, judgment, errors
  current run state and proposed/applied state deltas
  immutable artifact refs, hashes, tool/model/prompt/asset versions
  backend ID, endpoint alias, model revision, tool policy, invocation ID

Learning assets (Git-versioned learning/):
  KB cases + provenance
  Strategy definitions + applicable conditions + versions
  Skill instructions/scripts + versions
  Candidate deltas + evaluation results
```

Store all raw evidence references independently of summaries. Each stage is persisted before the next runs; restarting after a crash can inspect completed stages without silently redoing side-effectful work. Tools need idempotency or explicit review for retries.

## 9. RSI extension (after execution baseline works)

Do **not** put automated self-improvement logic inside the Orchestrator. Add an offline command/pipeline:

```text
Archived Run(s)
    → propose candidate KB / Strategy / Skill delta
    → validate allowed target + syntactic checks
    → evaluate on held-out tasks against the pinned baseline
    → approve/promote versioned learning asset OR reject
    → future Runs use the approved asset snapshot
```

A small later module set can be `rsi/propose.py`, `rsi/evaluate.py`, `rsi/promote.py`. Promotion writes only under `learning/{knowledge,strategies,skills}`; never modifies `prompts/`, core Python runtime/orchestrator, or criteria. A candidate Skill script must execute in a constrained workspace during evaluation.

## 10. Proposed build sequence and acceptance criteria

**PR 1 — Runnable mock loop (first priority)**
- Implement contracts, fixed prompt files, Orchestrator, Runtime wrapper, Store, CLI.
- Use mock Planner/Executor/Judger: `CONTINUE` once and `COMPLETE` on the second round.
- Verify run event order and that each output exists on disk before the next invocation.

**PR 2a — Claude Agent SDK backend**
- Implement `AgentBackend`, `ClaudeSdkBackend`, `BackendFactory`, role configuration + versioned role prompts.
- Ensure fresh invocation per role; give each role only its allowed tools; retain model/backend provenance.
- Exercise one debugging task end-to-end with real tool evidence (not a model's claim of success).

**PR 2b — Universal local backend + hybrid routing**
- Implement OpenAI-compatible client for LM Studio, Ollama and vLLM plus bounded `LocalAgentBackend` tool loop.
- Verify JSON output and tool round trips per model; test backends per role, hybrid mode, and wrong/unsupported model capabilities.
- Keep Store/Context/Orchestrator unchanged when switching backends.

**PR 3 — Reliability gates**
- Resume after failure, timeout / max-iteration tests, safe retries, immutable artifacts, `COMPLETE` gate, asset snapshot provenance.
- Add an evaluation fixture for both successful and inconclusive Judger outcomes.

**PR 4 — RSI learning (separate from execution loop)**
- Candidate Learning Delta format + scope guard; baseline-vs-candidate evaluation; promote/reject/rollback.
- Demonstrate a proposed Strategy change that stays a candidate until the tests pass.

**Acceptance checklist:** one-command local run; three independently testable agents; typed contracts; run survives Judger failure without losing Executor artifacts; Run State and Learning Assets are isolated; Planner can reference version-pinned KB/Strategies/Skills; no auto modification of fixed role prompts or criteria.

## 11. Decisions to confirm before coding

1. **Backend baseline:** implement Claude Agent SDK first (PR 2a) and one local host (e.g. LM Studio) in PR 2b, then verify Ollama/vLLM by configuration + capability tests.
2. **Execution sandbox:** default to local, allowlisted tools in a per-run workspace; decide later whether container execution is required.
3. **RSI cadence:** manual/offline promotion first; no automatic promote from a single successful Run.

**Recommendation:** start with PR 1 (mock loop), then PR 2a (Claude Agent SDK), PR 2b (OpenAI-compatible local and hybrid), PR 3 (reliability) and PR 4 (RSI). This preserves one minimal P→E→J loop while keeping backends swappable.