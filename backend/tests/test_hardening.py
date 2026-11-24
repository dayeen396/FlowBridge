import asyncio
from uuid import uuid4

import pytest
from cryptography.fernet import InvalidToken

from app.contracts import Envelope, TriggerPayload, TriggerType
from app.contracts.params import McpToolParams, SendEmailParams
from app.credentials import decrypt_credential, encrypt_credential
from app.database import ExecutionRecord, NodeRunRecord
from app.nodes.base import NodeContext
from app.nodes.builtin import MCPToolNode, SendEmailNode


class FakeCredentials:
    async def get(self, _credential_id: str) -> dict:
        return {"host": "smtp.example.com", "username": "flow@example.com", "password": "secret"}


def node_context() -> NodeContext:
    return NodeContext(
        execution_id=str(uuid4()),
        workflow_id=str(uuid4()),
        user_id=str(uuid4()),
        node_id="action",
        attempt=1,
        trigger=TriggerPayload(type=TriggerType.MANUAL, data={}),
        node_outputs={},
        credentials=FakeCredentials(),
    )


def test_send_email_node_sends_and_returns_delivery_result(monkeypatch):
    sent = []

    async def fake_to_thread(function, *args, **kwargs):
        sent.append(function)

    monkeypatch.setattr("app.nodes.builtin.asyncio.to_thread", fake_to_thread)
    result = asyncio.run(SendEmailNode().execute(
        Envelope.empty(),
        SendEmailParams(credential_id="smtp-id", to="receiver@example.com", subject="Hello", body="Workflow ran"),
        node_context(),
    ))

    assert sent
    assert result.outputs["main"].first()["sent"] is True
    assert result.outputs["main"].first()["to"] == "receiver@example.com"


def test_mcp_tool_node_calls_selected_cached_tool(monkeypatch):
    calls = []

    async def fake_call_saved_tool(context, server_id, tool_name, arguments):
        calls.append((context.node_id, server_id, tool_name, arguments))
        return {"content": [{"type": "text", "text": "done"}], "structured": {"ok": True}, "is_error": False}

    monkeypatch.setattr("app.nodes.builtin.call_saved_tool", fake_call_saved_tool)
    result = asyncio.run(MCPToolNode().execute(
        Envelope.empty(),
        McpToolParams(server_id=str(uuid4()), tool_name="lookup", arguments={"id": 4}),
        node_context(),
    ))

    assert calls[0][2:] == ("lookup", {"id": 4})
    assert result.outputs["main"].first()["structured"] == {"ok": True}


def test_credential_encryption_round_trips_without_plaintext(monkeypatch):
    monkeypatch.setenv("FLOWBRIDGE_SECRET_KEY", "test-key-for-credential-encryption")
    secret = {"password": "not-stored-in-plaintext", "host": "smtp.example.com"}

    encrypted = encrypt_credential(secret)

    assert b"not-stored-in-plaintext" not in encrypted
    assert decrypt_credential(encrypted) == secret


def test_credentials_cannot_be_decrypted_after_key_rotation(monkeypatch):
    monkeypatch.setenv("FLOWBRIDGE_SECRET_KEY", "first-key")
    encrypted = encrypt_credential({"token": "value"})
    monkeypatch.setenv("FLOWBRIDGE_SECRET_KEY", "second-key")

    with pytest.raises(InvalidToken):
        decrypt_credential(encrypted)


def test_execution_and_node_run_lookup_indexes_exist():
    execution_indexes = {index.name: index for index in ExecutionRecord.__table__.indexes}
    node_run_indexes = {index.name: index for index in NodeRunRecord.__table__.indexes}

    assert "idx_executions_user_created_at" in execution_indexes
    assert "user_id" in {column.name for column in execution_indexes["idx_executions_user_created_at"].columns}
    assert "created_at" in str(execution_indexes["idx_executions_user_created_at"].expressions[1])
    assert "idx_node_runs_execution" in node_run_indexes
    assert [column.name for column in node_run_indexes["idx_node_runs_execution"].columns] == ["execution_id"]
