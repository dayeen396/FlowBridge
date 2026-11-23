"""First executable node implementations for the workflow engine."""
from __future__ import annotations

import json
import operator
from typing import Any

import httpx
from pydantic import BaseModel

from app.contracts import (
    Envelope,
    ErrorCode,
    NodeResult,
    NodeType,
)
from app.contracts.params import (
    ConditionParams,
    DataProcessingParams,
    HttpRequestParams,
    ManualTriggerParams,
    WebhookParams,
)
from app.nodes.base import BaseNode, NodeContext, NodeFailure, register_node


@register_node
class ManualTriggerNode(BaseNode):
    node_type = NodeType.MANUAL_TRIGGER

    async def execute(
        self, input: Envelope, params: BaseModel, ctx: NodeContext
    ) -> NodeResult:
        assert isinstance(params, ManualTriggerParams)
        return NodeResult.ok(input)


@register_node
class WebhookTriggerNode(BaseNode):
    node_type = NodeType.WEBHOOK

    async def execute(
        self, input: Envelope, params: BaseModel, ctx: NodeContext
    ) -> NodeResult:
        assert isinstance(params, WebhookParams)
        return NodeResult.ok(input)


@register_node
class DataProcessingNode(BaseNode):
    node_type = NodeType.DATA_PROCESSING

    async def execute(
        self, input: Envelope, params: BaseModel, ctx: NodeContext
    ) -> NodeResult:
        assert isinstance(params, DataProcessingParams)
        results: list[dict[str, Any]] = []
        for input_item in input.items:
            data = dict(input_item.data)
            if params.operation == "set":
                data.update(params.fields)
            elif params.operation == "pick":
                data = {key: data[key] for key in params.keys if key in data}
            elif params.operation == "rename":
                for old, new in params.fields.items():
                    if old in data:
                        data[new] = data.pop(old)
            elif params.operation == "parse_json":
                value = data.get(params.source_key)
                if not isinstance(value, str):
                    raise NodeFailure(
                        ErrorCode.VALIDATION_ERROR,
                        f"field '{params.source_key}' must contain a JSON string",
                    )
                try:
                    data[params.source_key] = json.loads(value)
                except json.JSONDecodeError as exc:
                    raise NodeFailure(
                        ErrorCode.VALIDATION_ERROR,
                        f"field '{params.source_key}' is not valid JSON",
                    ) from exc
            results.append(data)
        return NodeResult.ok(Envelope.of(*results, node_id=ctx.node_id))


@register_node
class ConditionNode(BaseNode):
    node_type = NodeType.CONDITION

    async def execute(
        self, input: Envelope, params: BaseModel, ctx: NodeContext
    ) -> NodeResult:
        assert isinstance(params, ConditionParams)
        if not input.items:
            return NodeResult.ok()
        left, right = params.left, params.right
        try:
            matched = _evaluate_condition(left, params.operator, right)
        except (TypeError, ValueError) as exc:
            raise NodeFailure(
                ErrorCode.VALIDATION_ERROR,
                f"condition operator '{params.operator}' cannot compare these values",
            ) from exc
        handle = "true" if matched else "false"
        return NodeResult.ok(**{handle: Envelope(items=input.items, meta=input.meta)})


@register_node
class HttpRequestNode(BaseNode):
    node_type = NodeType.HTTP_REQUEST

    async def execute(
        self, input: Envelope, params: BaseModel, ctx: NodeContext
    ) -> NodeResult:
        assert isinstance(params, HttpRequestParams)
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                response = await client.request(
                    params.method,
                    params.url,
                    headers=params.headers,
                    params=params.query,
                    json=params.body,
                )
        except httpx.TimeoutException as exc:
            raise NodeFailure(
                ErrorCode.HTTP_ERROR,
                "HTTP request timed out",
                retryable=True,
            ) from exc
        except httpx.RequestError as exc:
            raise NodeFailure(
                ErrorCode.HTTP_ERROR,
                f"HTTP request failed: {exc}",
                retryable=True,
            ) from exc

        try:
            body: Any = response.json()
        except ValueError:
            body = response.text
        if params.fail_on_error_status and response.status_code >= 400:
            raise NodeFailure(
                ErrorCode.HTTP_ERROR,
                f"HTTP request returned status {response.status_code}",
                retryable=response.status_code >= 500,
                status=response.status_code,
                body=body,
            )
        return NodeResult.ok(
            Envelope.of(
                {
                    "status": response.status_code,
                    "headers": dict(response.headers),
                    "body": body,
                },
                node_id=ctx.node_id,
            )
        )


def _evaluate_condition(left: Any, condition_operator: str, right: Any) -> bool:
    if condition_operator == "equals":
        return left == right
    if condition_operator == "not_equals":
        return left != right
    if condition_operator == "contains":
        return right in left
    if condition_operator == "gt":
        return operator.gt(left, right)
    if condition_operator == "gte":
        return operator.ge(left, right)
    if condition_operator == "lt":
        return operator.lt(left, right)
    if condition_operator == "lte":
        return operator.le(left, right)
    if condition_operator == "is_empty":
        return left is None or left == "" or left == [] or left == {}
    if condition_operator == "is_not_empty":
        return not (left is None or left == "" or left == [] or left == {})
    raise ValueError(f"unsupported condition operator: {condition_operator}")
