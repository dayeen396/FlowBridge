"""Per-node `params` models.

Params are validated at RUN time, after {{ expressions }} are rendered
(a field typed `int` may hold "{{ item.count }}" while the user is editing).
The JSON Schema of each model drives the frontend's config form.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ManualTriggerParams(_Params):
    sample_data: dict[str, Any] = Field(default_factory=dict, description="Data emitted when run manually.")


class WebhookParams(_Params):
    http_method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "POST"


class HttpRequestParams(_Params):
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    url: str = Field(description="Full URL. Supports {{ expressions }}.")
    headers: dict[str, str] = Field(default_factory=dict)
    query: dict[str, Any] = Field(default_factory=dict)
    body: Any = None
    fail_on_error_status: bool = Field(default=True, description="Treat HTTP status >= 400 as a failure.")


class DataProcessingParams(_Params):
    operation: Literal["set", "pick", "rename", "parse_json"] = "set"
    fields: dict[str, Any] = Field(default_factory=dict, description="set: name -> value. rename: old -> new.")
    keys: list[str] = Field(default_factory=list, description="pick: keys to keep.")
    source_key: str = Field(default="", description="parse_json: key holding a JSON string.")


ConditionOperator = Literal[
    "equals", "not_equals", "contains", "gt", "gte", "lt", "lte", "is_empty", "is_not_empty"
]


class ConditionParams(_Params):
    left: Any = Field(description="Usually an expression, e.g. {{ item.amount }}.")
    operator: ConditionOperator = "equals"
    right: Any = None


class SendEmailParams(_Params):
    credential_id: str = Field(description="Saved SMTP credential.")
    to: str
    subject: str
    body: str
    from_address: str | None = None


class McpToolParams(_Params):
    server_id: str = Field(description="A saved MCP server.")
    tool_name: str
    arguments: dict[str, Any] = Field(
        default_factory=dict,
        description="Validated against the tool's own input schema, cached from the server.",
    )
