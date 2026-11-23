"""Safe expression rendering for workflow node parameters."""
from __future__ import annotations

import re
from typing import Any

from app.nodes.base import NodeFailure
from app.contracts import ErrorCode

_EXPRESSION = re.compile(r"{{\s*(.*?)\s*}}")
_PATH = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*(?:(?:\.[A-Za-z_][A-Za-z0-9_-]*)|(?:\[\d+\]))*")


def render_value(value: Any, scope: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: render_value(child, scope) for key, child in value.items()}
    if isinstance(value, list):
        return [render_value(child, scope) for child in value]
    if not isinstance(value, str):
        return value

    matches = list(_EXPRESSION.finditer(value))
    if not matches:
        if "{{" in value or "}}" in value:
            raise _expression_failure("malformed expression")
        return value

    if len(matches) == 1 and matches[0].span() == (0, len(value)):
        return _resolve(matches[0].group(1), scope)

    pieces: list[str] = []
    cursor = 0
    for match in matches:
        pieces.append(value[cursor:match.start()])
        resolved = _resolve(match.group(1), scope)
        pieces.append("" if resolved is None else str(resolved))
        cursor = match.end()
    pieces.append(value[cursor:])
    rendered = "".join(pieces)
    if "{{" in rendered or "}}" in rendered:
        raise _expression_failure("malformed expression")
    return rendered


def _resolve(path: str, scope: dict[str, Any]) -> Any:
    if not _PATH.fullmatch(path):
        raise _expression_failure(f"invalid expression path '{path}'")

    tokens: list[str | int] = []
    for match in re.finditer(r"[A-Za-z_][A-Za-z0-9_-]*|\[(\d+)\]", path):
        token = match.group()
        tokens.append(int(token[1:-1]) if token.startswith("[") else token)

    if not tokens or tokens[0] not in scope:
        raise _expression_failure(f"unknown expression root in '{path}'")
    current: Any = scope[tokens[0]]
    for token in tokens[1:]:
        if current is None:
            return None
        if isinstance(token, int):
            if not isinstance(current, list):
                return None
            current = current[token] if token < len(current) else None
        elif isinstance(current, dict):
            current = current.get(token)
        else:
            return None
    return current


def _expression_failure(message: str) -> NodeFailure:
    return NodeFailure(ErrorCode.EXPRESSION_ERROR, message)
