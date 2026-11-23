"""Static metadata for every node type: outputs, trigger flag, params model."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from .models import NodeType
from .params import (
    ConditionParams,
    DataProcessingParams,
    HttpRequestParams,
    ManualTriggerParams,
    McpToolParams,
    SendEmailParams,
    WebhookParams,
)

Category = Literal["trigger", "logic", "action", "integration"]


@dataclass(frozen=True)
class NodeTypeSpec:
    type: NodeType
    label: str
    description: str
    category: Category
    outputs: tuple[str, ...]
    params_model: type[BaseModel]

    @property
    def is_trigger(self) -> bool:
        return self.category == "trigger"


NODE_TYPE_SPECS: dict[NodeType, NodeTypeSpec] = {
    s.type: s
    for s in (
        NodeTypeSpec(NodeType.MANUAL_TRIGGER, "Manual Trigger", "Start the workflow with a button.", "trigger", ("main",), ManualTriggerParams),
        NodeTypeSpec(NodeType.WEBHOOK, "Webhook", "Start the workflow from an HTTP request.", "trigger", ("main",), WebhookParams),
        NodeTypeSpec(NodeType.HTTP_REQUEST, "HTTP Request", "Call any HTTP API.", "action", ("main",), HttpRequestParams),
        NodeTypeSpec(NodeType.DATA_PROCESSING, "Data Processing", "Set, pick, rename or parse fields.", "logic", ("main",), DataProcessingParams),
        NodeTypeSpec(NodeType.CONDITION, "Condition", "Route items to the true or false branch.", "logic", ("true", "false"), ConditionParams),
        NodeTypeSpec(NodeType.SEND_EMAIL, "Send Email", "Send an email over SMTP.", "action", ("main",), SendEmailParams),
        NodeTypeSpec(NodeType.MCP_TOOL, "MCP Tool", "Call a tool on a connected MCP server.", "integration", ("main",), McpToolParams),
    )
}


def node_catalog() -> list[dict]:
    """Shape served by GET /node-types and consumed by the frontend editor."""
    return [
        {
            "type": s.type.value,
            "label": s.label,
            "description": s.description,
            "category": s.category,
            "is_trigger": s.is_trigger,
            "outputs": list(s.outputs),
            "params_schema": s.params_model.model_json_schema(),
        }
        for s in NODE_TYPE_SPECS.values()
    ]
