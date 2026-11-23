"""Structural validation of a workflow graph (run on every save and before every run)."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from .models import Workflow
from .specs import NODE_TYPE_SPECS


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    node_id: str | None = None
    edge_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


class WorkflowValidationError(Exception):
    def __init__(self, issues: list[ValidationIssue]):
        self.issues = issues
        super().__init__("; ".join(f"{i.code}: {i.message}" for i in issues))


def validate_workflow(wf: Workflow) -> list[ValidationIssue]:
    """Return every problem found (empty list = valid)."""
    issues: list[ValidationIssue] = []
    nodes = {}
    for n in wf.nodes:
        if n.id in nodes:
            issues.append(ValidationIssue("DUPLICATE_NODE_ID", f"node id '{n.id}' is used twice", node_id=n.id))
        nodes[n.id] = n

    seen_edge_ids: set[str] = set()
    seen_links: set[tuple[str, str, str]] = set()
    incoming: dict[str, int] = {nid: 0 for nid in nodes}
    adj: dict[str, list[str]] = {nid: [] for nid in nodes}

    for e in wf.edges:
        if e.id in seen_edge_ids:
            issues.append(ValidationIssue("DUPLICATE_EDGE_ID", f"edge id '{e.id}' is used twice", edge_id=e.id))
        seen_edge_ids.add(e.id)
        if e.source not in nodes or e.target not in nodes:
            issues.append(ValidationIssue("UNKNOWN_NODE", "edge points to a node that does not exist", edge_id=e.id))
            continue
        if e.source == e.target:
            issues.append(ValidationIssue("SELF_LOOP", "a node cannot connect to itself", edge_id=e.id))
            continue
        spec = NODE_TYPE_SPECS[nodes[e.source].type]
        if e.source_handle not in spec.outputs:
            issues.append(ValidationIssue(
                "BAD_SOURCE_HANDLE",
                f"'{e.source_handle}' is not an output of {spec.type.value} (valid: {', '.join(spec.outputs)})",
                edge_id=e.id,
            ))
        link = (e.source, e.source_handle, e.target)
        if link in seen_links:
            issues.append(ValidationIssue("DUPLICATE_EDGE", "the same connection exists twice", edge_id=e.id))
        seen_links.add(link)
        incoming[e.target] += 1
        adj[e.source].append(e.target)

    triggers = [n for n in nodes.values() if NODE_TYPE_SPECS[n.type].is_trigger]
    if len(triggers) != 1:
        issues.append(ValidationIssue("TRIGGER_COUNT", f"a workflow needs exactly one trigger node, found {len(triggers)}"))
    for t in triggers:
        if incoming[t.id]:
            issues.append(ValidationIssue("TRIGGER_HAS_INPUT", "a trigger node cannot have incoming connections", node_id=t.id))
    for n in nodes.values():
        if not NODE_TYPE_SPECS[n.type].is_trigger and incoming[n.id] == 0:
            issues.append(ValidationIssue("ORPHAN_NODE", f"'{n.name}' has no incoming connection", node_id=n.id))

    if _has_cycle(adj):
        issues.append(ValidationIssue("CYCLE", "the workflow contains a cycle; loops are not supported in v1"))
    elif len(triggers) == 1:
        reachable = _reachable(adj, triggers[0].id)
        for nid in nodes:
            if nid not in reachable and not NODE_TYPE_SPECS[nodes[nid].type].is_trigger and incoming[nid]:
                issues.append(ValidationIssue("UNREACHABLE", "node cannot be reached from the trigger", node_id=nid))
    return issues


def parse_workflow(data: dict[str, Any]) -> Workflow:
    """Parse + validate raw JSON. Raises WorkflowValidationError with all issues."""
    try:
        wf = Workflow.model_validate(data)
    except ValidationError as exc:
        issues = [
            ValidationIssue("SCHEMA_ERROR", f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}")
            for err in exc.errors()
        ]
        raise WorkflowValidationError(issues) from exc
    issues = validate_workflow(wf)
    if issues:
        raise WorkflowValidationError(issues)
    return wf


def topological_order(wf: Workflow) -> list[str]:
    """Deterministic execution order (Kahn). Assumes the workflow already validated."""
    indeg = {n.id: 0 for n in wf.nodes}
    adj: dict[str, list[str]] = {n.id: [] for n in wf.nodes}
    for e in wf.edges:
        adj[e.source].append(e.target)
        indeg[e.target] += 1
    order_index = {n.id: i for i, n in enumerate(wf.nodes)}
    queue = deque(sorted((i for i, d in indeg.items() if d == 0), key=order_index.get))
    out: list[str] = []
    while queue:
        cur = queue.popleft()
        out.append(cur)
        for nxt in sorted(adj[cur], key=order_index.get):
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                queue.append(nxt)
    return out


def _has_cycle(adj: dict[str, list[str]]) -> bool:
    indeg = {k: 0 for k in adj}
    for targets in adj.values():
        for t in targets:
            indeg[t] += 1
    queue = deque(k for k, d in indeg.items() if d == 0)
    visited = 0
    while queue:
        cur = queue.popleft()
        visited += 1
        for t in adj[cur]:
            indeg[t] -= 1
            if indeg[t] == 0:
                queue.append(t)
    return visited != len(adj)


def _reachable(adj: dict[str, list[str]], start: str) -> set[str]:
    seen = {start}
    queue = deque([start])
    while queue:
        for t in adj[queue.popleft()]:
            if t not in seen:
                seen.add(t)
                queue.append(t)
    return seen
