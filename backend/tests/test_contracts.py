import json
from pathlib import Path

import jsonschema
import pytest

from app.contracts import (
    NODE_TYPE_SPECS, Envelope, ErrorCode, NodeResult, NodeType, Workflow,
    WorkflowValidationError, parse_workflow, topological_order, validate_workflow,
)
from scripts.export_contracts import render

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = sorted((ROOT / "contracts" / "examples").glob("*.json"))


def load(name):
    return json.loads((ROOT / "contracts" / "examples" / name).read_text())


def base():
    return {
        "schema_version": 1, "name": "t",
        "nodes": [
            {"id": "t", "type": "manual_trigger", "name": "T"},
            {"id": "a", "type": "http_request", "name": "A", "params": {"url": "https://x.io"}},
        ],
        "edges": [{"id": "e1", "source": "t", "target": "a"}],
    }


def codes(data):
    try:
        parse_workflow(data)
    except WorkflowValidationError as exc:
        return {i.code for i in exc.issues}
    return set()


# ---------------------------------------------------------- examples
@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_examples_are_valid(path):
    wf = parse_workflow(json.loads(path.read_text()))
    assert wf.nodes


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_examples_match_json_schema(path):
    schema = json.loads((ROOT / "contracts" / "workflow.schema.json").read_text())
    jsonschema.validate(json.loads(path.read_text()), schema)


def test_example_count():
    assert len(EXAMPLES) == 3


def test_every_node_type_has_a_spec_and_an_example_or_catalog_entry():
    assert set(NODE_TYPE_SPECS) == set(NodeType)
    used = {n.type for p in EXAMPLES for n in parse_workflow(json.loads(p.read_text())).nodes}
    assert used == set(NodeType), "every node type should appear in at least one example"


# ---------------------------------------------------------- graph rules
def test_valid_base():
    assert codes(base()) == set()


def test_duplicate_node_id():
    d = base(); d["nodes"].append({"id": "a", "type": "http_request", "name": "dup"})
    assert "DUPLICATE_NODE_ID" in codes(d)


def test_no_trigger():
    d = base(); d["nodes"][0]["type"] = "http_request"
    assert "TRIGGER_COUNT" in codes(d)


def test_two_triggers():
    d = base(); d["nodes"].append({"id": "w", "type": "webhook", "name": "W"})
    assert "TRIGGER_COUNT" in codes(d)


def test_orphan():
    d = base(); d["nodes"].append({"id": "o", "type": "http_request", "name": "O"})
    assert "ORPHAN_NODE" in codes(d)


def test_unknown_node_in_edge():
    d = base(); d["edges"].append({"id": "e2", "source": "a", "target": "zzz"})
    assert "UNKNOWN_NODE" in codes(d)


def test_self_loop():
    d = base(); d["edges"].append({"id": "e2", "source": "a", "target": "a"})
    assert "SELF_LOOP" in codes(d)


def test_cycle():
    d = base()
    d["nodes"].append({"id": "b", "type": "http_request", "name": "B"})
    d["edges"] += [{"id": "e2", "source": "a", "target": "b"}, {"id": "e3", "source": "b", "target": "a"}]
    assert "CYCLE" in codes(d)


def test_bad_source_handle():
    d = base(); d["edges"][0]["source_handle"] = "true"
    assert "BAD_SOURCE_HANDLE" in codes(d)


def test_condition_handles():
    d = base(); d["nodes"][1] = {"id": "a", "type": "condition", "name": "C", "params": {"left": 1}}
    d["nodes"].append({"id": "b", "type": "http_request", "name": "B"})
    d["edges"].append({"id": "e2", "source": "a", "source_handle": "true", "target": "b"})
    assert codes(d) == set()


def test_trigger_cannot_have_input():
    d = base(); d["edges"].append({"id": "e2", "source": "a", "target": "t"})
    assert {"TRIGGER_HAS_INPUT", "CYCLE"} <= codes(d)


def test_duplicate_edge():
    d = base(); d["edges"].append({"id": "e2", "source": "t", "target": "a"})
    assert "DUPLICATE_EDGE" in codes(d)


def test_schema_errors_are_reported():
    d = base(); d["nodes"][0]["id"] = "bad id!"
    assert "SCHEMA_ERROR" in codes(d)


def test_unknown_field_rejected():
    d = base(); d["nodes"][0]["colour"] = "red"
    assert "SCHEMA_ERROR" in codes(d)


def test_wrong_schema_version():
    d = base(); d["schema_version"] = 2
    assert "SCHEMA_ERROR" in codes(d)


def test_topological_order_is_deterministic():
    wf = parse_workflow(load("02-webhook-condition-email.json"))
    assert topological_order(wf) == ["hook", "shape", "check", "mail", "log"]


# ---------------------------------------------------------- envelope / result
def test_envelope_helpers():
    env = Envelope.of({"a": 1}, {"a": 2}, source="x")
    assert [i.data["a"] for i in env.items] == [1, 2]
    assert env.meta == {"source": "x"}
    assert Envelope.empty().first() == {}


def test_envelope_round_trip():
    env = Envelope.of({"a": {"b": [1, 2]}})
    assert Envelope.model_validate_json(env.model_dump_json()) == env


def test_node_result_rules():
    ok = NodeResult.ok(Envelope.of({"x": 1}))
    assert ok.status == "success" and "main" in ok.outputs
    branch = NodeResult.ok(true=Envelope.of({"x": 1}))
    assert set(branch.outputs) == {"true"}
    bad = NodeResult.fail(ErrorCode.HTTP_ERROR, "boom", retryable=True, status=502)
    assert bad.error.retryable and bad.error.details == {"status": 502}
    with pytest.raises(ValueError):
        NodeResult(status="failed")
    with pytest.raises(ValueError):
        NodeResult(status="success", error=bad.error)


# ---------------------------------------------------------- params / catalog
def test_params_models_accept_example_params():
    for path in EXAMPLES:
        wf = parse_workflow(json.loads(path.read_text()))
        for n in wf.nodes:
            NODE_TYPE_SPECS[n.type].params_model.model_validate(n.params)


def test_generated_contract_files_are_up_to_date():
    for name, text in render().items():
        assert (ROOT / "contracts" / name).read_text() == text, f"{name} is stale: run scripts/export_contracts.py"


# ---------------------------------------------------------- node registry
def test_registry():
    from app.nodes.base import BaseNode, NodeContext, get_node_class, register_node
    import app.nodes.base as base_mod

    saved = dict(base_mod._REGISTRY)
    base_mod._REGISTRY.clear()
    try:
        @register_node
        class Echo(BaseNode):
            node_type = NodeType.MANUAL_TRIGGER

            async def execute(self, input, params, ctx: NodeContext):
                return NodeResult.ok(input)

        assert get_node_class(NodeType.MANUAL_TRIGGER) is Echo
        with pytest.raises(ValueError):
            register_node(Echo)
        with pytest.raises(LookupError):
            get_node_class(NodeType.WEBHOOK)
    finally:
        base_mod._REGISTRY.clear()
        base_mod._REGISTRY.update(saved)
