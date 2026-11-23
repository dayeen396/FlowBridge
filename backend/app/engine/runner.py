"""In-memory async workflow execution. Persistence is owned by later API/worker steps."""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping

from pydantic import ValidationError

from app.contracts import (
    NODE_TYPE_SPECS,
    Edge,
    Envelope,
    ErrorCode,
    ExecutionStatus,
    Node,
    NodeResult,
    NodeRunStatus,
    NodeType,
    TriggerPayload,
    TriggerType,
    Workflow,
    parse_workflow,
    topological_order,
)
from app.engine.expressions import render_value
from app.nodes.base import (
    BaseNode,
    CredentialStore,
    NodeContext,
    NodeFailure,
    get_node_class,
)

_PER_ITEM_NODE_TYPES = {NodeType.CONDITION, NodeType.DATA_PROCESSING}
_logger = logging.getLogger("flowbridge.engine")


@dataclass
class WorkflowRunResult:
    status: ExecutionStatus
    outputs: dict[str, dict[str, Envelope]] = field(default_factory=dict)
    node_inputs: dict[str, Envelope] = field(default_factory=dict)
    node_statuses: dict[str, NodeRunStatus] = field(default_factory=dict)
    node_results: dict[str, NodeResult] = field(default_factory=dict)
    attempts: dict[str, int] = field(default_factory=dict)
    attempt_results: dict[str, list[NodeResult]] = field(default_factory=dict)


class _UnavailableCredentials:
    async def get(self, credential_id: str) -> dict[str, Any]:
        raise NodeFailure(
            ErrorCode.CREDENTIAL_ERROR,
            f"credential '{credential_id}' is unavailable in the in-memory engine",
        )


class WorkflowEngine:
    """Validate a workflow and run its nodes in deterministic topological order."""

    async def run(
        self,
        workflow: Workflow | dict[str, Any],
        trigger_payload: TriggerPayload | dict[str, Any] | None = None,
        *,
        execution_id: str | None = None,
        workflow_id: str = "local",
        user_id: str = "local",
        credentials: CredentialStore | None = None,
    ) -> WorkflowRunResult:
        parsed = _parse_input(workflow)
        trigger_node = next(
            node for node in parsed.nodes if NODE_TYPE_SPECS[node.type].is_trigger
        )
        payload = _make_trigger_payload(trigger_node, trigger_payload)
        run_id = execution_id or str(uuid.uuid4())
        credential_store = credentials if credentials is not None else _UnavailableCredentials()
        node_by_id = {node.id: node for node in parsed.nodes}
        result = WorkflowRunResult(status=ExecutionStatus.SUCCESS)

        for node_id in topological_order(parsed):
            node = node_by_id[node_id]
            incoming = _input_for_node(node, parsed.edges, result.outputs, payload)
            result.node_inputs[node_id] = incoming
            if not NODE_TYPE_SPECS[node.type].is_trigger and not incoming.items:
                result.node_statuses[node_id] = NodeRunStatus.SKIPPED
                continue

            try:
                node_result, attempts, attempt_results = await self._execute_node(
                    node,
                    incoming,
                    payload,
                    result.outputs,
                    run_id,
                    workflow_id,
                    user_id,
                    credential_store,
                )
            except NodeFailure as failure:
                node_result, attempts = failure.to_result(), 1
                attempt_results = [node_result]

            result.attempts[node_id] = attempts
            result.attempt_results[node_id] = attempt_results
            result.node_results[node_id] = node_result
            if node_result.status == "success":
                result.node_statuses[node_id] = NodeRunStatus.SUCCESS
                result.outputs[node_id] = node_result.outputs
                continue

            result.node_statuses[node_id] = NodeRunStatus.FAILED
            if node.on_error == "stop":
                result.status = ExecutionStatus.FAILED
                for remaining_id in topological_order(parsed):
                    if remaining_id not in result.node_statuses:
                        result.node_statuses[remaining_id] = NodeRunStatus.SKIPPED
                break

        return result

    async def _execute_node(
        self,
        node: Node,
        incoming: Envelope,
        trigger: TriggerPayload,
        node_outputs: Mapping[str, Mapping[str, Envelope]],
        execution_id: str,
        workflow_id: str,
        user_id: str,
        credentials: CredentialStore,
    ) -> tuple[NodeResult, int, list[NodeResult]]:
        try:
            node_class = get_node_class(node.type)
        except LookupError as exc:
            failure = NodeResult.fail(ErrorCode.NODE_ERROR, str(exc))
            return failure, 1, [failure]
        instance: BaseNode = node_class()
        if node.type in _PER_ITEM_NODE_TYPES:
            return await self._execute_per_item(
                instance,
                node,
                incoming,
                trigger,
                node_outputs,
                execution_id,
                workflow_id,
                user_id,
                credentials,
            )

        scope = _expression_scope(incoming, trigger, node_outputs, execution_id)
        try:
            rendered = render_value(node.params, scope)
            params = NODE_TYPE_SPECS[node.type].params_model.model_validate(rendered)
        except NodeFailure as failure:
            node_result = failure.to_result()
            return node_result, 1, [node_result]
        except ValidationError as exc:
            issues = [
                {"loc": list(error["loc"]), "msg": error["msg"]}
                for error in exc.errors()
            ]
            node_result = NodeResult.fail(
                ErrorCode.VALIDATION_ERROR,
                "node parameters are invalid",
                issues=issues,
            )
            return node_result, 1, [node_result]

        return await self._execute_with_retries(
            instance, node, incoming, params, trigger, node_outputs,
            execution_id, workflow_id, user_id, credentials,
        )

    async def _execute_per_item(
        self,
        instance: BaseNode,
        node: Node,
        incoming: Envelope,
        trigger: TriggerPayload,
        node_outputs: Mapping[str, Mapping[str, Envelope]],
        execution_id: str,
        workflow_id: str,
        user_id: str,
        credentials: CredentialStore,
    ) -> tuple[NodeResult, int, list[NodeResult]]:
        histories: list[list[NodeResult]] = []
        for item in incoming.items:
            single_input = Envelope(items=[item], meta=incoming.meta)
            try:
                rendered = render_value(
                    node.params,
                    _expression_scope(single_input, trigger, node_outputs, execution_id),
                )
                params = NODE_TYPE_SPECS[node.type].params_model.model_validate(rendered)
            except NodeFailure as failure:
                node_result = failure.to_result()
                histories.append([node_result])
                combined = _combine_attempt_histories(histories)
                return combined[-1], len(combined), combined
            except ValidationError as exc:
                issues = [
                    {"loc": list(error["loc"]), "msg": error["msg"]}
                    for error in exc.errors()
                ]
                node_result = NodeResult.fail(
                    ErrorCode.VALIDATION_ERROR,
                    "node parameters are invalid",
                    issues=issues,
                )
                histories.append([node_result])
                combined = _combine_attempt_histories(histories)
                return combined[-1], len(combined), combined

            node_result, _, item_history = await self._execute_with_retries(
                instance, node, single_input, params, trigger, node_outputs,
                execution_id, workflow_id, user_id, credentials,
            )
            histories.append(item_history)
            if node_result.status == "failed":
                combined = _combine_attempt_histories(histories)
                return combined[-1], len(combined), combined

        combined = _combine_attempt_histories(histories)
        return combined[-1], len(combined), combined

    async def _execute_with_retries(
        self,
        instance: BaseNode,
        node: Node,
        incoming: Envelope,
        params: Any,
        trigger: TriggerPayload,
        node_outputs: Mapping[str, Mapping[str, Envelope]],
        execution_id: str,
        workflow_id: str,
        user_id: str,
        credentials: CredentialStore,
    ) -> tuple[NodeResult, int, list[NodeResult]]:
        spec = NODE_TYPE_SPECS[node.type]
        history: list[NodeResult] = []
        for attempt in range(1, node.retry.max_attempts + 1):
            context = NodeContext(
                execution_id=execution_id,
                workflow_id=workflow_id,
                user_id=user_id,
                node_id=node.id,
                attempt=attempt,
                trigger=trigger,
                node_outputs=node_outputs,
                credentials=credentials,
            )
            try:
                node_result = await asyncio.wait_for(
                    instance.execute(incoming, params, context),
                    timeout=node.timeout_seconds,
                )
            except asyncio.TimeoutError:
                node_result = NodeResult.fail(
                    ErrorCode.TIMEOUT,
                    f"node exceeded its {node.timeout_seconds}s timeout",
                    retryable=True,
                )
            except NodeFailure as failure:
                node_result = failure.to_result()
            except Exception:
                _logger.exception("Unexpected error in node %s (%s)", node.id, node.type.value)
                node_result = NodeResult.fail(
                    ErrorCode.INTERNAL,
                    f"node '{node.id}' failed unexpectedly",
                )
            history.append(node_result)

            if node_result.status == "success":
                invalid_handles = set(node_result.outputs) - set(spec.outputs)
                if invalid_handles:
                    node_result = NodeResult.fail(
                        ErrorCode.NODE_ERROR,
                        f"node returned unsupported output handles: {sorted(invalid_handles)}",
                    )
                    history[-1] = node_result
                    return node_result, attempt, history
                return node_result, attempt, history

            error = node_result.error
            if error is None or not error.retryable or attempt == node.retry.max_attempts:
                return node_result, attempt, history
            delay = node.retry.backoff_seconds * node.retry.backoff_multiplier ** (attempt - 1)
            if delay:
                await asyncio.sleep(delay)

        raise RuntimeError("retry loop exited without a node result")


def _combine_attempt_histories(histories: list[list[NodeResult]]) -> list[NodeResult]:
    attempt_count = max((len(history) for history in histories), default=0)
    combined: list[NodeResult] = []
    for attempt_index in range(attempt_count):
        results = [
            history[min(attempt_index, len(history) - 1)]
            for history in histories
            if history
        ]
        failed = next((result for result in results if result.status == "failed"), None)
        if failed is not None:
            combined.append(failed)
            continue
        outputs: dict[str, list[Any]] = {}
        for result in results:
            for handle, envelope in result.outputs.items():
                outputs.setdefault(handle, []).extend(envelope.items)
        combined.append(
            NodeResult.ok(
                **{
                    handle: Envelope(items=items)
                    for handle, items in outputs.items()
                    if items
                }
            )
        )
    return combined


def _parse_input(workflow: Workflow | dict[str, Any]) -> Workflow:
    definition = workflow.model_dump(mode="json") if isinstance(workflow, Workflow) else workflow
    if not isinstance(definition, dict):
        raise TypeError("workflow must be a Workflow or a JSON object")
    return parse_workflow(definition)


def _make_trigger_payload(
    trigger_node: Node,
    payload: TriggerPayload | dict[str, Any] | None,
) -> TriggerPayload:
    trigger_type = (
        TriggerType.MANUAL
        if trigger_node.type == NodeType.MANUAL_TRIGGER
        else TriggerType.WEBHOOK
    )
    if payload is None:
        raw_data = trigger_node.params.get("sample_data", {}) if trigger_type == TriggerType.MANUAL else {}
        data = raw_data if isinstance(raw_data, dict) else {}
        return TriggerPayload(type=trigger_type, data=data)
    parsed = payload if isinstance(payload, TriggerPayload) else TriggerPayload.model_validate(payload)
    if parsed.type != trigger_type:
        raise ValueError(f"{trigger_node.type.value} requires a {trigger_type.value} trigger payload")
    return parsed


def _input_for_node(
    node: Node,
    edges: list[Edge],
    outputs: Mapping[str, Mapping[str, Envelope]],
    trigger: TriggerPayload,
) -> Envelope:
    if NODE_TYPE_SPECS[node.type].is_trigger:
        return Envelope.of(trigger.data, node_id=node.id)
    items = []
    for edge in edges:
        if edge.target != node.id:
            continue
        envelope = outputs.get(edge.source, {}).get(edge.source_handle)
        if envelope is not None and envelope.items:
            items.extend(envelope.items)
    return Envelope(items=items, meta={"node_id": node.id})


def _expression_scope(
    incoming: Envelope,
    trigger: TriggerPayload,
    node_outputs: Mapping[str, Mapping[str, Envelope]],
    execution_id: str,
) -> dict[str, Any]:
    node_values: dict[str, dict[str, Any]] = {}
    for node_id, handles in node_outputs.items():
        serialized = {
            handle: envelope.model_dump(mode="json")
            for handle, envelope in handles.items()
        }
        if "main" in serialized:
            serialized["items"] = serialized["main"]["items"]
        node_values[node_id] = serialized
    return {
        "item": incoming.first(),
        "items": [item.data for item in incoming.items],
        "trigger": trigger.data,
        "nodes": node_values,
        "execution": {"id": execution_id},
    }
