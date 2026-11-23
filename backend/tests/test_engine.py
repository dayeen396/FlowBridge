import asyncio

import httpx
import pytest

from app.contracts import (
    ErrorCode,
    ExecutionStatus,
    NodeRunStatus,
    TriggerType,
    WorkflowValidationError,
)
from app.engine import WorkflowEngine


def graph(nodes, edges):
    return {"schema_version": 1, "name": "Engine test", "nodes": nodes, "edges": edges}


def node(node_id, node_type, params=None, **extra):
    return {
        "id": node_id,
        "type": node_type,
        "name": node_id,
        "params": params or {},
        **extra,
    }


def edge(edge_id, source, target, source_handle="main"):
    return {
        "id": edge_id,
        "source": source,
        "source_handle": source_handle,
        "target": target,
        "target_handle": "main",
    }


def run(workflow, trigger_payload=None):
    return asyncio.run(WorkflowEngine().run(workflow, trigger_payload))


def test_runs_nodes_in_topological_order_and_passes_outputs():
    workflow = graph(
        [
            node("finish", "data_processing", {"fields": {"total": "{{ item.amount }}"}}),
            node("shape", "data_processing", {"fields": {"amount": 42}}),
            node("start", "manual_trigger", {"sample_data": {"source": "test"}}),
        ],
        [edge("e1", "shape", "finish"), edge("e2", "start", "shape")],
    )

    result = run(workflow)

    assert result.status == ExecutionStatus.SUCCESS
    assert result.node_statuses == {
        "start": NodeRunStatus.SUCCESS,
        "shape": NodeRunStatus.SUCCESS,
        "finish": NodeRunStatus.SUCCESS,
    }
    assert result.outputs["start"]["main"].first() == {"source": "test"}
    assert result.outputs["finish"]["main"].first() == {"source": "test", "amount": 42, "total": 42}


def test_rejects_cycles_before_executing_nodes():
    workflow = graph(
        [
            node("start", "manual_trigger"),
            node("a", "data_processing"),
            node("b", "data_processing"),
        ],
        [edge("e1", "start", "a"), edge("e2", "a", "b"), edge("e3", "b", "a")],
    )

    with pytest.raises(WorkflowValidationError, match="CYCLE"):
        run(workflow)


@pytest.mark.parametrize(
    ("amount", "active", "skipped"),
    [(1200, "large", "small"), (100, "small", "large")],
)
def test_condition_runs_only_the_active_branch(amount, active, skipped):
    workflow = graph(
        [
            node("start", "manual_trigger"),
            node("check", "condition", {"left": "{{ item.amount }}", "operator": "gt", "right": 1000}),
            node("large", "data_processing", {"fields": {"route": "large"}}),
            node("small", "data_processing", {"fields": {"route": "small"}}),
            node("after_small", "data_processing", {"fields": {"done": True}}),
        ],
        [
            edge("e1", "start", "check"),
            edge("e2", "check", "large", "true"),
            edge("e3", "check", "small", "false"),
            edge("e4", "small", "after_small"),
        ],
    )

    result = run(workflow, {"type": "manual", "data": {"amount": amount}})

    assert result.status == ExecutionStatus.SUCCESS
    assert result.node_statuses[active] == NodeRunStatus.SUCCESS
    assert result.node_statuses[skipped] == NodeRunStatus.SKIPPED
    assert result.node_statuses["check"] == NodeRunStatus.SUCCESS
    if skipped == "small":
        assert result.node_statuses["after_small"] == NodeRunStatus.SKIPPED


def test_condition_routes_each_merged_input_item_independently():
    workflow = graph(
        [
            node("start", "manual_trigger"),
            node("low_source", "data_processing", {"fields": {"amount": 5}}),
            node("high_source", "data_processing", {"fields": {"amount": 20}}),
            node("check", "condition", {"left": "{{ item.amount }}", "operator": "gt", "right": 10}),
            node("high", "data_processing", {"fields": {"route": "high"}}),
            node("low", "data_processing", {"fields": {"route": "low"}}),
        ],
        [
            edge("e1", "start", "low_source"),
            edge("e2", "start", "high_source"),
            edge("e3", "low_source", "check"),
            edge("e4", "high_source", "check"),
            edge("e5", "check", "high", "true"),
            edge("e6", "check", "low", "false"),
        ],
    )

    result = run(workflow)

    assert [item.data for item in result.outputs["high"]["main"].items] == [
        {"amount": 20, "route": "high"}
    ]
    assert [item.data for item in result.outputs["low"]["main"].items] == [
        {"amount": 5, "route": "low"}
    ]


def test_data_processing_supports_pick_rename_and_parse_json():
    workflow = graph(
        [
            node("start", "manual_trigger"),
            node("pick", "data_processing", {"operation": "pick", "keys": ["payload"]}),
            node("rename", "data_processing", {"operation": "rename", "fields": {"payload": "parsed"}}),
            node("parse", "data_processing", {"operation": "parse_json", "source_key": "parsed"}),
        ],
        [
            edge("e1", "start", "pick"),
            edge("e2", "pick", "rename"),
            edge("e3", "rename", "parse"),
        ],
    )

    result = run(
        workflow,
        {"type": "manual", "data": {"payload": '{"count": 3}', "drop": "ignored", "other": 1}},
    )

    assert result.outputs["parse"]["main"].first() == {"parsed": {"count": 3}}


def test_expression_preserves_types_and_resolves_lists():
    workflow = graph(
        [
            node("start", "manual_trigger"),
            node("shape", "data_processing", {"fields": {"second": "{{ item.values[1] }}", "id": "{{ execution.id }}"}}),
        ],
        [edge("e1", "start", "shape")],
    )

    result = asyncio.run(
        WorkflowEngine().run(
            workflow,
            {"type": "manual", "data": {"values": [1, 7]}},
            execution_id="run-123",
        )
    )

    assert result.outputs["shape"]["main"].first() == {
        "values": [1, 7],
        "second": 7,
        "id": "run-123",
    }


def test_expression_can_read_a_finished_node_main_output():
    workflow = graph(
        [
            node("start", "manual_trigger"),
            node("shape", "data_processing", {"fields": {"amount": 7}}),
            node("copy", "data_processing", {"fields": {"previous": "{{ nodes.shape.items[0].data.amount }}"}}),
        ],
        [edge("e1", "start", "shape"), edge("e2", "shape", "copy")],
    )

    result = run(workflow)

    assert result.outputs["copy"]["main"].first()["previous"] == 7


def test_http_request_returns_status_headers_and_json_body(monkeypatch):
    original_client = httpx.AsyncClient

    def client_factory(**kwargs):
        return original_client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, json={"ok": True}, headers={"x-test": "yes"})
            ),
            **kwargs,
        )

    monkeypatch.setattr("app.nodes.builtin.httpx.AsyncClient", client_factory)
    workflow = graph(
        [
            node("start", "manual_trigger"),
            node("request", "http_request", {"url": "https://service.test/"}),
        ],
        [edge("e1", "start", "request")],
    )

    result = run(workflow)

    response = result.outputs["request"]["main"].first()
    assert response["status"] == 200
    assert response["body"] == {"ok": True}
    assert response["headers"]["x-test"] == "yes"


def test_http_request_retries_retryable_server_errors(monkeypatch):
    original_client = httpx.AsyncClient
    calls = 0

    def handler(_request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, json={"error": "temporary"})
        return httpx.Response(200, json={"ok": True})

    def client_factory(**kwargs):
        return original_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("app.nodes.builtin.httpx.AsyncClient", client_factory)
    workflow = graph(
        [
            node("start", "manual_trigger"),
            node(
                "request",
                "http_request",
                {"url": "https://service.test/"},
                retry={"max_attempts": 2, "backoff_seconds": 0, "backoff_multiplier": 1},
            ),
        ],
        [edge("e1", "start", "request")],
    )

    result = run(workflow)

    assert result.status == ExecutionStatus.SUCCESS
    assert result.attempts["request"] == 2
    assert [attempt.status for attempt in result.attempt_results["request"]] == [
        "failed",
        "success",
    ]
    assert calls == 2
    assert result.outputs["request"]["main"].first()["body"] == {"ok": True}


def test_http_failure_stops_execution_and_marks_later_nodes_skipped(monkeypatch):
    original_client = httpx.AsyncClient

    def client_factory(**kwargs):
        return original_client(
            transport=httpx.MockTransport(lambda _request: httpx.Response(400, text="bad request")),
            **kwargs,
        )

    monkeypatch.setattr("app.nodes.builtin.httpx.AsyncClient", client_factory)
    workflow = graph(
        [
            node("start", "manual_trigger"),
            node("request", "http_request", {"url": "https://service.test/"}),
            node("after", "data_processing"),
        ],
        [edge("e1", "start", "request"), edge("e2", "request", "after")],
    )

    result = run(workflow)

    assert result.status == ExecutionStatus.FAILED
    assert result.node_results["request"].error.code == ErrorCode.HTTP_ERROR
    assert result.node_statuses["after"] == NodeRunStatus.SKIPPED


def test_continue_policy_keeps_other_branches_running(monkeypatch):
    original_client = httpx.AsyncClient

    def client_factory(**kwargs):
        return original_client(
            transport=httpx.MockTransport(lambda _request: httpx.Response(400, text="bad request")),
            **kwargs,
        )

    monkeypatch.setattr("app.nodes.builtin.httpx.AsyncClient", client_factory)
    workflow = graph(
        [
            node("start", "manual_trigger"),
            node(
                "request",
                "http_request",
                {"url": "https://service.test/"},
                on_error="continue",
            ),
            node("after", "data_processing"),
            node("parallel", "data_processing", {"fields": {"continued": True}}),
        ],
        [
            edge("e1", "start", "request"),
            edge("e2", "request", "after"),
            edge("e3", "start", "parallel"),
        ],
    )

    result = run(workflow)

    assert result.status == ExecutionStatus.SUCCESS
    assert result.node_statuses["request"] == NodeRunStatus.FAILED
    assert result.node_statuses["after"] == NodeRunStatus.SKIPPED
    assert result.outputs["parallel"]["main"].first()["continued"] is True


def test_webhook_trigger_accepts_contract_payload():
    workflow = graph(
        [node("hook", "webhook"), node("shape", "data_processing", {"fields": {"seen": True}})],
        [edge("e1", "hook", "shape")],
    )

    result = run(
        workflow,
        {
            "type": TriggerType.WEBHOOK.value,
            "data": {"method": "POST", "body": {"value": 2}},
        },
    )

    assert result.outputs["shape"]["main"].first() == {
        "method": "POST",
        "body": {"value": 2},
        "seen": True,
    }
