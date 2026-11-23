"""Core FlowBridge contracts: enums, data envelope, workflow definition.

This file is the single source of truth. The JSON Schema in /contracts and the
frontend node catalog are generated from it (see backend/scripts/export_contracts.py).
Do not change a field here without bumping SCHEMA_VERSION and updating docs/00-contracts.md.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = 1
ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"


# ----------------------------------------------------------------- enums
class NodeType(str, Enum):
    MANUAL_TRIGGER = "manual_trigger"
    WEBHOOK = "webhook"
    HTTP_REQUEST = "http_request"
    DATA_PROCESSING = "data_processing"
    CONDITION = "condition"
    SEND_EMAIL = "send_email"
    MCP_TOOL = "mcp_tool"


class TriggerType(str, Enum):
    MANUAL = "manual"
    WEBHOOK = "webhook"


class ExecutionStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


class NodeRunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class ErrorCode(str, Enum):
    VALIDATION_ERROR = "VALIDATION_ERROR"      # bad params after rendering
    EXPRESSION_ERROR = "EXPRESSION_ERROR"      # bad {{ }} syntax
    HTTP_ERROR = "HTTP_ERROR"                  # http_request failed / bad status
    EMAIL_ERROR = "EMAIL_ERROR"                # SMTP failure
    MCP_ERROR = "MCP_ERROR"                    # MCP server / tool failure
    CREDENTIAL_ERROR = "CREDENTIAL_ERROR"      # missing or unreadable credential
    TIMEOUT = "TIMEOUT"                        # node exceeded timeout_seconds
    NODE_ERROR = "NODE_ERROR"                  # any other node-level failure
    INTERNAL = "INTERNAL"                      # engine bug, never the user's fault


# ------------------------------------------------------- data envelope
class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Item(_Strict):
    """One record flowing between nodes. `data` is always a JSON object."""

    data: dict[str, Any] = Field(default_factory=dict)


class Envelope(_Strict):
    """The ONLY thing nodes pass to each other."""

    items: list[Item] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def of(cls, *records: dict[str, Any], **meta: Any) -> "Envelope":
        return cls(items=[Item(data=r) for r in records], meta=dict(meta))

    @classmethod
    def empty(cls) -> "Envelope":
        return cls()

    def first(self) -> dict[str, Any]:
        return self.items[0].data if self.items else {}


class NodeError(_Strict):
    code: ErrorCode
    message: str
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class NodeResult(_Strict):
    """What a node's execute() returns.

    `outputs` maps an output handle ("main", "true", "false", ...) to an Envelope.
    A handle that is ABSENT is inactive: nodes wired only to it are skipped.
    """

    status: Literal["success", "failed"] = "success"
    outputs: dict[str, Envelope] = Field(default_factory=dict)
    error: NodeError | None = None

    @model_validator(mode="after")
    def _check(self) -> "NodeResult":
        if self.status == "failed" and self.error is None:
            raise ValueError("a failed NodeResult needs an error")
        if self.status == "success" and self.error is not None:
            raise ValueError("a successful NodeResult cannot carry an error")
        return self

    @classmethod
    def ok(cls, main: Envelope | None = None, **handles: Envelope) -> "NodeResult":
        outputs = dict(handles)
        if main is not None:
            outputs["main"] = main
        return cls(status="success", outputs=outputs)

    @classmethod
    def fail(cls, code: ErrorCode, message: str, retryable: bool = False, **details: Any) -> "NodeResult":
        return cls(status="failed", error=NodeError(code=code, message=message, retryable=retryable, details=details))


class TriggerPayload(_Strict):
    type: TriggerType
    data: dict[str, Any] = Field(default_factory=dict)


# ------------------------------------------------- workflow definition
class Position(_Strict):
    x: float = 0
    y: float = 0


class RetryPolicy(_Strict):
    max_attempts: int = Field(default=1, ge=1, le=10)
    backoff_seconds: float = Field(default=2.0, ge=0, le=300)
    backoff_multiplier: float = Field(default=2.0, ge=1, le=10)


class Node(_Strict):
    id: str = Field(pattern=ID_PATTERN)
    type: NodeType
    name: str = Field(min_length=1, max_length=100)
    params: dict[str, Any] = Field(default_factory=dict)
    position: Position = Field(default_factory=Position)
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    timeout_seconds: int = Field(default=30, ge=1, le=3600)
    # "stop": a failure fails the whole execution.
    # "continue": the node is marked failed, the execution carries on,
    #             and everything wired only to this node is skipped.
    on_error: Literal["stop", "continue"] = "stop"


class Edge(_Strict):
    id: str = Field(pattern=ID_PATTERN)
    source: str
    target: str
    source_handle: str = "main"
    target_handle: Literal["main"] = "main"


class Workflow(_Strict):
    schema_version: Literal[1] = SCHEMA_VERSION
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    nodes: list[Node] = Field(min_length=1, max_length=100)
    edges: list[Edge] = Field(default_factory=list, max_length=300)
