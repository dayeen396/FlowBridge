"""The contract every node implements. Step 2 adds the real nodes; this file never changes shape."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar, Mapping, Protocol

from pydantic import BaseModel

from app.contracts import Envelope, ErrorCode, NodeResult, NodeType, TriggerPayload
from app.contracts.specs import NODE_TYPE_SPECS


class CredentialStore(Protocol):
    """Resolves a saved credential (decrypted) for the execution's owner only."""

    async def get(self, credential_id: str) -> dict[str, Any]: ...


@dataclass
class NodeContext:
    execution_id: str
    workflow_id: str
    user_id: str
    node_id: str
    attempt: int                                   # 1-based
    trigger: TriggerPayload
    node_outputs: Mapping[str, Mapping[str, Envelope]]   # node_id -> handle -> Envelope (finished nodes)
    credentials: CredentialStore
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("flowbridge.node"))


class NodeFailure(Exception):
    """Raise inside execute() for an expected failure; the engine turns it into a failed NodeResult."""

    def __init__(self, code: ErrorCode, message: str, retryable: bool = False, **details: Any):
        super().__init__(message)
        self.code, self.message, self.retryable, self.details = code, message, retryable, details

    def to_result(self) -> NodeResult:
        return NodeResult.fail(self.code, self.message, self.retryable, **self.details)


class BaseNode(ABC):
    """One class per node type. Stateless: build it once, call execute() many times.

    The engine, not the node, handles: expression rendering, params validation,
    retries, timeouts, persistence and branch skipping.
    """

    node_type: ClassVar[NodeType]

    @abstractmethod
    async def execute(self, input: Envelope, params: BaseModel, ctx: NodeContext) -> NodeResult:
        """`input` = merged items of all active parents (trigger nodes get the trigger payload as one item).
        `params` = already rendered and validated with NODE_TYPE_SPECS[node_type].params_model."""


_REGISTRY: dict[NodeType, type[BaseNode]] = {}


def register_node(cls: type[BaseNode]) -> type[BaseNode]:
    nt = getattr(cls, "node_type", None)
    if nt is None or nt not in NODE_TYPE_SPECS:
        raise ValueError(f"{cls.__name__}.node_type must be a NodeType listed in NODE_TYPE_SPECS")
    if nt in _REGISTRY:
        raise ValueError(f"node type '{nt.value}' is already registered")
    _REGISTRY[nt] = cls
    return cls


def get_node_class(node_type: NodeType) -> type[BaseNode]:
    try:
        return _REGISTRY[node_type]
    except KeyError:
        raise LookupError(f"no implementation registered for node type '{node_type.value}'") from None


def registered_types() -> set[NodeType]:
    return set(_REGISTRY)
