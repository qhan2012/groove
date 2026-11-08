import json

import pytest

from groove.agents.mock import MockExecutor, MockJudger, MockPlanner
from groove.contracts import ActionPlan, ExecutionResult, Judgment
from groove.orchestrator import run
from groove.runtime import Runtime
from groove.store import SqliteRunStore, StoreError


def harness(tmp_path, *, judger=None, executor=None):
    store = SqliteRunStore(tmp_path)
    run_id = store.create_run("Mock workflow", ["Verified evidence after two rounds"])
    runtime = Runtime(store, MockPlanner(), executor or MockExecutor(), judger or MockJudger())
    return store, run_id, runtime


def test_two_round_loop_and_durable_event_order(tmp_path):
    store, run_id, runtime = harness(tmp_path)
    outcome = run(run_id, runtime)
    assert outcome.status == "COMPLETED"
    assert outcome.completed_iterations == 2
    assert outcome.state == {"mock_last_iteration": 2}
    history = store.load_context(run_id).history
    assert [(e["iteration"], e["stage"]) for e in history] == [
        (1, "plan"), (1, "execution"), (1, "judgment"),
        (2, "plan"), (2, "execution"), (2, "judgment"),
    ]
    for event in history:
        if event["stage"] == "execution":
            assert all(store.has_artifact(run_id, ref) for ref in event["payload"]["artifact_refs"])
    # Reopening the store reads the same persisted results.
    reopened = SqliteRunStore(tmp_path)
    assert reopened.outcome(run_id) == outcome


def test_iteration_limit(tmp_path):
    store, run_id, runtime = harness(tmp_path)
    outcome = run(run_id, runtime, max_iterations=1)
    assert outcome.status == "LIMIT_REACHED"
    assert outcome.completed_iterations == 1


def test_judger_failure_does_not_lose_executor_artifact(tmp_path):
    class BrokenJudger:
        def run(self, plan, result, context):
            raise RuntimeError("judger unavailable")

    store, run_id, runtime = harness(tmp_path, judger=BrokenJudger())
    with pytest.raises(RuntimeError, match="judger unavailable"):
        run(run_id, runtime)
    history = store.load_context(run_id).history
    assert [e["stage"] for e in history] == ["plan", "execution", "error"]
    assert store.has_artifact(run_id, history[1]["payload"]["artifact_refs"][0])
    assert store.outcome(run_id).status == "ERROR"


def test_false_complete_rejected(tmp_path):
    class BadJudger:
        def run(self, plan, result, context):
            return Judgment(verification="PASS", decision="COMPLETE",
                            reason="No evidence", evidence_refs=[])

    store, run_id, runtime = harness(tmp_path, judger=BadJudger())
    with pytest.raises(StoreError, match="requires evidence"):
        run(run_id, runtime)
    assert store.outcome(run_id).status == "ERROR"


def test_run_metadata_cannot_be_modified_by_judger(tmp_path):
    class MaliciousJudger:
        def run(self, plan, result, context):
            return Judgment(verification="PASS", decision="CONTINUE", reason="invalid delta",
                            proposed_run_state_delta={"goal": "changed"})

    store, run_id, runtime = harness(tmp_path, judger=MaliciousJudger())
    with pytest.raises(StoreError, match="immutable run metadata"):
        run(run_id, runtime)
    ctx = store.load_context(run_id)
    assert ctx.goal == "Mock workflow"
    assert ctx.acceptance_criteria == ["Verified evidence after two rounds"]
    assert store.outcome(run_id).status == "ERROR"


def test_schema_rejects_extra_fields():
    with pytest.raises(Exception):
        ActionPlan.model_validate({"objective": "x", "action": "a", "expected_observation": "b",
                                   "unknown": 99})


def test_artifact_integrity_check(tmp_path):
    store, run_id, runtime = harness(tmp_path)
    run(run_id, runtime)
    ref = store.load_context(run_id).history[1]["payload"]["artifact_refs"][0]
    digest = ref.removeprefix("sha256:")
    p = tmp_path / "runs" / run_id / "artifacts" / digest
    assert json.loads(p.read_text())["mock"] is True
    p.write_text("tampered")
    assert not store.has_artifact(run_id, ref)


def test_invalid_run_spec(tmp_path):
    store = SqliteRunStore(tmp_path)
    with pytest.raises(ValueError):
        store.create_run("", ["criterion"])
    with pytest.raises(ValueError):
        store.create_run("goal", [])
