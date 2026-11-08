"""Hermetic tests: no Claude account or model server required."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from groove.agents.llm import LLMExecutor, LLMJudger, LLMPlanner
from groove.backends.base import BackendError, Tool
from groove.backends.claude import ClaudeBackend
from groove.backends.local import LocalBackend
from groove.config import GrooveConfig, load_config
from groove.contracts import RunContext
from groove.factory import make_runtime
from groove.orchestrator import run
from groove.store import SqliteRunStore


def result(content=None, calls=None):
    msg = SimpleNamespace(content=content, tool_calls=calls)
    msg.model_dump = lambda exclude_none=True: {"role": "assistant", "content": content,
                                                  "tool_calls": [x.__dict__ for x in calls or []]}
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


class FakeOpenAI:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.answers.pop(0)


def test_local_returns_json_and_is_stateless():
    api = FakeOpenAI([result('{"hello":"world"}')])
    backend = LocalBackend(model="my-local", base_url="http://localhost:1234/v1", client=api)
    value = backend.generate(system_prompt="demo", payload={"goal": "test"}, response_schema={"type": "object"})
    assert value == {"hello": "world"}
    assert len(api.calls[0]["messages"]) == 2
    assert "tools" not in api.calls[0]


def test_local_tool_call_allowlist_and_limit():
    def call(name, args):
        func = SimpleNamespace(name=name, arguments=json.dumps(args))
        return SimpleNamespace(id="call1", type="function", function=func)

    api = FakeOpenAI([result(calls=[call("publish_artifact", {"name": "a", "content": "ok"})]),
                      result('{"ok":true}')])
    saved = []
    tool = Tool("publish_artifact", "emit", {"type": "object"},
                lambda args: saved.append(args) or {"artifact_ref": "sha256:fake"})
    backend = LocalBackend(model="test", base_url="http://localhost:8000/v1", client=api)
    assert backend.generate(system_prompt="s", payload={}, response_schema={}, tools=(tool,)) == {"ok": True}
    assert saved == [{"name": "a", "content": "ok"}]
    assert api.calls[1]["messages"][-1]["role"] == "tool"

    denied = FakeOpenAI([result(calls=[call("shell", {"cmd": "rm -rf /"})])])
    with pytest.raises(BackendError, match="not permitted"):
        LocalBackend(model="x", base_url="http://localhost/v1", client=denied).generate(
            system_prompt="s", payload={}, response_schema={}, tools=(tool,))

    over = FakeOpenAI([result(calls=[call("publish_artifact", {})])])
    with pytest.raises(BackendError, match="max_tool_rounds"):
        LocalBackend(model="x", base_url="http://localhost/v1", client=over, max_tool_rounds=0).generate(
            system_prompt="s", payload={}, response_schema={}, tools=(tool,))


def test_local_invalid_json_fails_closed():
    client = FakeOpenAI([result("not json")])
    with pytest.raises(BackendError, match="valid JSON"):
        LocalBackend(model="x", base_url="http://localhost/v1", client=client).generate(
            system_prompt="s", payload={}, response_schema={})


class FakeBackend:
    supports_callback_tools = True

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def generate(self, *, system_prompt, payload, response_schema, tools=()):
        self.calls.append((system_prompt, payload, response_schema, tools))
        value = self.replies.pop(0)
        if callable(value):
            return value(tools)
        return value


def ctx():
    return RunContext(run_id="id1", goal="Make evidence", acceptance_criteria=["Evidence exists"],
                      iteration=1, state={}, history=[])


def test_real_role_wrappers_publish_evidence(tmp_path):
    writer = SqliteRunStore(tmp_path)
    run_id = writer.create_run("Make evidence", ["Evidence exists"])
    context = writer.load_context(run_id)
    planner = LLMPlanner(FakeBackend([{"objective": "make evidence", "action": "generate",
                                       "expected_observation": "file"}]))
    plan = planner.run(context)

    def executor_reply(tools):
        response = tools[0].handler({"name": "evidence.md", "content": "proof"})
        return {"status": "SUCCEEDED", "artifact_refs": [response["artifact_ref"]],
                "observations": ["published"], "artifacts": []}

    executor = LLMExecutor(FakeBackend([executor_reply]))
    class Writer:
        def write(self, name, content, media_type="text/plain"):
            return writer.write_artifact(run_id, name, content, media_type)
    result = executor.run(plan, context, Writer())
    assert writer.has_artifact(run_id, result.artifact_refs[0])
    judge = LLMJudger(FakeBackend([{"verification": "PASS", "decision": "COMPLETE",
                                    "reason": "Evidence emitted", "evidence_refs": result.artifact_refs}]))
    assert judge.run(plan, result, context).decision == "COMPLETE"


def test_executor_rejects_fabricated_refs(tmp_path):
    executor = LLMExecutor(FakeBackend([{"status": "SUCCEEDED", "artifact_refs": ["sha256:fake"]}]))
    with pytest.raises(BackendError, match="did not publish"):
        executor.run(SimpleNamespace(model_dump=lambda **kw: {}), ctx(), SimpleNamespace(write=lambda *a: "x"))


def test_inline_artifacts_are_size_limited(tmp_path):
    executor = LLMExecutor(FakeBackend([{"status": "SUCCEEDED", "artifacts":
                                          [{"name": "test", "content": "x" * 11}]}]), max_artifact_bytes=10)
    with pytest.raises(BackendError, match="size budget"):
        executor.run(SimpleNamespace(model_dump=lambda **kw: {}), ctx(), SimpleNamespace(write=lambda *a: "x"))


def test_config_validation_and_factory(tmp_path):
    data = {
        "backends": {"local": {"kind": "openai_compatible", "base_url": "http://localhost:1234/v1"}},
        "agents": {k: {"backend": "local", "model": "test"} for k in ("planner", "executor", "judger")},
    }
    config = GrooveConfig.model_validate(data)
    from groove.agents.llm import LLMPlanner
    # Building the client should be offline: only an API call is deferred.
    assert isinstance(make_runtime(SqliteRunStore(tmp_path), config).planner, LLMPlanner)
    data["agents"]["judger"]["backend"] = "missing"
    with pytest.raises(ValueError, match="Undefined backend"):
        GrooveConfig.model_validate(data)


def test_claude_backend_uses_fresh_limited_options():
    capture = []
    async def fake_query(*, prompt, options):
        capture.append((prompt, options))
        yield SimpleNamespace(structured_output={"status": "ok"}, is_error=False)
    backend = ClaudeBackend(model="sonnet", max_turns=3,
                            query_function=fake_query, options_factory=lambda **kw: kw)
    assert backend.generate(system_prompt="role", payload={"goal": "g"}, response_schema={}) == {"status": "ok"}
    opts = capture[0][1]
    assert opts["permission_mode"] == "dontAsk"
    assert opts["tools"] == []
    assert opts["continue_conversation"] is False
    with pytest.raises(BackendError, match="Callable tools are not supported"):
        backend.generate(system_prompt="role", payload={}, response_schema={}, tools=(Tool("x", "x", {}, lambda a: {}),))


def test_end_to_end_llm_wrappers_with_mocked_local_api(tmp_path):
    from groove.runtime import Runtime
    # Same API transport can back multiple role-specific adapters.
    api = FakeOpenAI([
        result(json.dumps({"objective": "produce report", "action": "write report",
                           "expected_observation": "report artifact"})),
        result(json.dumps({"status": "SUCCEEDED", "observations": ["written"],
                           "artifacts": [{"name": "report.md", "content": "evidence"}]})),
        result(json.dumps({"verification": "PASS", "decision": "COMPLETE",
                           "reason": "Report is present", "evidence_refs": []})),
    ])
    # Judger can't invent artifact refs, so a decision without evidence must fail.
    mk = lambda: LocalBackend(model="dummy", base_url="http://localhost/v1", client=api)
    store = SqliteRunStore(tmp_path)
    run_id = store.create_run("Write a report", ["Report artifact exists"])
    runtime = Runtime(store, LLMPlanner(mk()), LLMExecutor(mk()), LLMJudger(mk()))
    from groove.store import StoreError
    with pytest.raises(StoreError, match="requires evidence"):
        run(run_id, runtime, max_iterations=1)
    assert store.outcome(run_id).status == "ERROR"
    assert len(api.calls) == 3
    assert store.has_artifact(run_id, store.load_context(run_id).history[1]["payload"]["artifact_refs"][0])


def test_load_yaml_config_and_tool_workspace_gate(tmp_path):
    conf = tmp_path / "cfg.yaml"
    conf.write_text("""
backends:
  local:
    kind: openai_compatible
    base_url: http://localhost:8000/v1
agents:
  planner: {backend: local, model: test}
  executor: {backend: local, model: test}
  judger: {backend: local, model: test}
""")
    assert load_config(conf).agents.executor.model == "test"
    with pytest.raises(ValueError, match="explicit workspace"):
        GrooveConfig.model_validate({"backends": {"claude": {"kind": "claude", "allowed_tools": ["Bash"]}},
                                     "agents": {r: {"backend": "claude", "model": "sonnet"}
                                                for r in ("planner", "executor", "judger")}})
