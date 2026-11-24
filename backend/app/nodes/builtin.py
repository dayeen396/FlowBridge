"""First executable node implementations for the workflow engine."""
from __future__ import annotations

import json
import operator
import asyncio
import ipaddress
import smtplib
import socket
import ssl
from email.message import EmailMessage
from email.utils import make_msgid
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
    McpToolParams,
    SendEmailParams,
    WebhookParams,
)
from app.nodes.base import BaseNode, NodeContext, NodeFailure, register_node
from app.mcp_client import call_saved_tool


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


async def _resolve_host_addresses(host: str, port: int) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    results = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses = []
    for result in results:
        address = result[4][0].split("%", 1)[0]
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            continue
        if parsed not in addresses:
            addresses.append(parsed)
    return addresses


async def _resolve_public_ip(host: str, port: int) -> str:
    host = host.strip("[]")
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            addresses = await _resolve_host_addresses(host, port)
        except OSError as exc:
            raise NodeFailure(ErrorCode.HTTP_ERROR, "HTTP request host could not be resolved", retryable=True) from exc
    if not addresses:
        raise NodeFailure(ErrorCode.HTTP_ERROR, "HTTP request host has no usable address", retryable=True)
    if any(not address.is_global for address in addresses):
        raise NodeFailure(
            ErrorCode.HTTP_ERROR,
            "HTTP request targets must resolve only to public IP addresses",
        )
    return str(addresses[0])


@register_node
class HttpRequestNode(BaseNode):
    node_type = NodeType.HTTP_REQUEST

    async def execute(
        self, input: Envelope, params: BaseModel, ctx: NodeContext
    ) -> NodeResult:
        assert isinstance(params, HttpRequestParams)
        try:
            target = httpx.URL(params.url)
        except httpx.InvalidURL as exc:
            raise NodeFailure(ErrorCode.HTTP_ERROR, "HTTP request URL is invalid") from exc
        if target.scheme not in {"http", "https"} or not target.host or target.username or target.password:
            raise NodeFailure(ErrorCode.HTTP_ERROR, "HTTP request URL must be HTTP(S) without embedded credentials")
        port = target.port or (443 if target.scheme == "https" else 80)
        hostname = target.host.strip("[]")
        resolved_ip = await _resolve_public_ip(hostname, port)
        request_url = target.copy_with(host=resolved_ip)
        host_header = f"[{hostname}]" if ":" in hostname else hostname
        if target.port is not None:
            host_header = f"{host_header}:{target.port}"
        request_headers = {key: value for key, value in params.headers.items() if key.lower() != "host"}
        request_headers["Host"] = host_header
        extensions = {"sni_hostname": hostname} if target.scheme == "https" else {}
        try:
            async with httpx.AsyncClient(timeout=None, trust_env=False) as client:
                response = await client.request(
                    params.method,
                    request_url,
                    headers=request_headers,
                    params=params.query,
                    json=params.body,
                    extensions=extensions,
                    follow_redirects=False,
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


@register_node
class SendEmailNode(BaseNode):
    node_type = NodeType.SEND_EMAIL

    async def execute(self, input: Envelope, params: BaseModel, ctx: NodeContext) -> NodeResult:
        assert isinstance(params, SendEmailParams)
        credential = await ctx.credentials.get(params.credential_id)
        required = ("host", "username", "password")
        if any(not isinstance(credential.get(key), str) or not credential[key] for key in required):
            raise NodeFailure(ErrorCode.CREDENTIAL_ERROR, "SMTP credential is incomplete")
        try:
            port = int(credential.get("port", 587))
            timeout = max(1, int(credential.get("timeout_seconds", 20)))
            use_ssl = bool(credential.get("use_ssl", False))
        except (TypeError, ValueError):
            raise NodeFailure(ErrorCode.CREDENTIAL_ERROR, "SMTP credential settings are invalid") from None
        message = EmailMessage()
        message["To"] = params.to
        message["From"] = params.from_address or credential.get("from_address") or credential["username"]
        message["Subject"] = params.subject
        message["Message-ID"] = make_msgid()
        message.set_content(params.body)

        def send() -> None:
            if use_ssl:
                with smtplib.SMTP_SSL(credential["host"], port, timeout=timeout, context=ssl.create_default_context()) as client:
                    client.login(credential["username"], credential["password"])
                    client.send_message(message)
            else:
                with smtplib.SMTP(credential["host"], port, timeout=timeout) as client:
                    client.ehlo()
                    if credential.get("starttls", True):
                        client.starttls(context=ssl.create_default_context())
                        client.ehlo()
                    client.login(credential["username"], credential["password"])
                    client.send_message(message)

        try:
            await asyncio.to_thread(send)
        except (smtplib.SMTPAuthenticationError, smtplib.SMTPRecipientsRefused, smtplib.SMTPHeloError) as exc:
            raise NodeFailure(ErrorCode.EMAIL_ERROR, f"SMTP rejected the email: {exc}") from exc
        except (smtplib.SMTPException, OSError, TimeoutError) as exc:
            raise NodeFailure(ErrorCode.EMAIL_ERROR, f"SMTP delivery failed: {exc}", retryable=True) from exc
        return NodeResult.ok(Envelope.of({"sent": True, "to": params.to, "subject": params.subject, "message_id": message["Message-ID"]}, node_id=ctx.node_id))


@register_node
class MCPToolNode(BaseNode):
    node_type = NodeType.MCP_TOOL

    async def execute(self, input: Envelope, params: BaseModel, ctx: NodeContext) -> NodeResult:
        assert isinstance(params, McpToolParams)
        result = await call_saved_tool(ctx, params.server_id, params.tool_name, params.arguments)
        return NodeResult.ok(Envelope.of(result, node_id=ctx.node_id))


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
