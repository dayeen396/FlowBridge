"""Small adapter around the official MCP Python client (Streamable HTTP and SSE only)."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, TYPE_CHECKING
from uuid import UUID

import httpx
from jsonschema import ValidationError as JsonSchemaValidationError
from jsonschema import validate as validate_json_schema
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client

from app.contracts import ErrorCode
from app.database import MCPServerRecord, SessionLocal

if TYPE_CHECKING:
    from app.nodes.base import NodeContext


def _model_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True)
    return dict(value)


def auth_headers(credential: dict[str, Any] | None) -> dict[str, str]:
    if not credential:
        return {}
    headers = credential.get("headers", {})
    result = {str(key): str(value) for key, value in headers.items()} if isinstance(headers, dict) else {}
    token = credential.get("bearer_token")
    if isinstance(token, str) and token and not any(key.lower() == "authorization" for key in result):
        result["Authorization"] = f"Bearer {token}"
    return result


@asynccontextmanager
async def open_session(server: dict[str, Any], credential: dict[str, Any] | None = None) -> AsyncIterator[ClientSession]:
    headers = auth_headers(credential)
    if server["transport"] == "streamable_http":
        async with httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(20, read=60)) as http_client:
            transport = streamable_http_client(server["url"], http_client=http_client)
            async with transport as (read_stream, write_stream, _get_session_id):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    yield session
        return
    transport = sse_client(url=server["url"], headers=headers, timeout=20, sse_read_timeout=60)
    async with transport as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            yield session


async def discover_tools(server: dict[str, Any], credential: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    async with open_session(server, credential) as session:
        response = await session.list_tools()
        tools = []
        for tool in response.tools:
            raw = _model_dict(tool)
            schema = raw.get("inputSchema", raw.get("input_schema", {"type": "object", "properties": {}}))
            tools.append({
                "name": raw.get("name", ""),
                "description": raw.get("description", "") or "",
                "input_schema": schema,
            })
        return tools


async def call_tool(
    server: dict[str, Any],
    credential: dict[str, Any] | None,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    async with open_session(server, credential) as session:
        result = await session.call_tool(tool_name, arguments)
        raw = _model_dict(result)
        content = raw.get("content", [])
        structured = raw.get("structuredContent", raw.get("structured_content"))
        return {
            "content": content,
            "structured": structured,
            "is_error": bool(raw.get("isError", raw.get("is_error", False))),
        }


async def call_saved_tool(ctx: NodeContext, server_id: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    from app.nodes.base import NodeFailure

    try:
        parsed_server_id = UUID(server_id)
    except ValueError:
        raise NodeFailure(ErrorCode.VALIDATION_ERROR, "MCP server id is invalid") from None
    with SessionLocal() as db:
        record = db.query(MCPServerRecord).filter_by(id=parsed_server_id, user_id=UUID(ctx.user_id)).first()
        if record is None:
            raise NodeFailure(ErrorCode.CREDENTIAL_ERROR, "MCP server is unavailable")
        server = {"id": str(record.id), "url": record.url, "transport": record.transport}
        credential_id = str(record.credential_id) if record.credential_id else None
        tools = list(record.tools_cache or [])
    tool = next((item for item in tools if item.get("name") == tool_name), None)
    if tool is None:
        raise NodeFailure(ErrorCode.VALIDATION_ERROR, "MCP tool is not in the server's cached tool list")
    try:
        validate_json_schema(arguments, tool.get("input_schema", {}))
    except JsonSchemaValidationError as exc:
        raise NodeFailure(ErrorCode.VALIDATION_ERROR, f"MCP tool arguments are invalid: {exc.message}") from exc
    credential = await ctx.credentials.get(credential_id) if credential_id else None
    try:
        result = await call_tool(server, credential, tool_name, arguments)
    except Exception as exc:
        raise NodeFailure(ErrorCode.MCP_ERROR, f"MCP tool call failed: {exc}", retryable=True) from exc
    return result
