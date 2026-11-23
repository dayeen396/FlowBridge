from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

import httpx
from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app.contracts import (
    NODE_TYPE_SPECS,
    NodeType,
    TriggerPayload,
    TriggerType,
    WorkflowValidationError,
    node_catalog,
    parse_workflow,
)
from app.database import (
    ExecutionRecord,
    NodeRunRecord,
    User,
    WorkflowRecord,
    get_db,
)
from app.worker import celery_app

logger = logging.getLogger("flowbridge.api")

app = FastAPI(title="FlowBridge API", version="0.1.0")
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("FLOWBRIDGE_SECRET_KEY", "local-dev-only-change-this-secret"),
    session_cookie="flowbridge_session",
    same_site="lax",
    https_only=os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true",
)

api = APIRouter(prefix="/api")


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: dict[str, Any] | None = None


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict):
        error = exc.detail
    else:
        code = {
            401: "UNAUTHORIZED",
            404: "NOT_FOUND",
            409: "VERSION_CONFLICT",
            422: "VALIDATION_ERROR",
            502: "OAUTH_ERROR",
            503: "SERVICE_UNAVAILABLE",
        }.get(exc.status_code, "HTTP_ERROR")
        error = {"code": code, "message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content={"error": error}, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    issues = [
        {"loc": list(issue["loc"]), "message": issue["msg"]}
        for issue in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "request validation failed",
                "issues": issues,
            }
        },
    )


@app.exception_handler(WorkflowValidationError)
async def workflow_validation_error_handler(
    _request: Request, exc: WorkflowValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "WORKFLOW_VALIDATION_ERROR",
                "message": "workflow definition is invalid",
                "issues": [issue.to_dict() for issue in exc.issues],
            }
        },
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled API error", exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "INTERNAL",
                "message": "an unexpected server error occurred",
            }
        },
    )


def _required_setting(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise HTTPException(
            status_code=503,
            detail={"code": "OAUTH_NOT_CONFIGURED", "message": f"{name} is not configured"},
        )
    return value


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    user_id = request.session.get("user_id")
    if not isinstance(user_id, str):
        raise HTTPException(status_code=401, detail="authentication required")
    try:
        parsed_user_id = UUID(user_id)
    except ValueError:
        request.session.clear()
        raise HTTPException(status_code=401, detail="authentication required") from None
    user = db.get(User, parsed_user_id)
    if user is None:
        request.session.clear()
        raise HTTPException(status_code=401, detail="authentication required")
    return user


def _public_user(user: User) -> dict[str, Any]:
    return {
        "id": str(user.id),
        "github_id": user.github_id,
        "login": user.login,
        "name": user.name,
        "email": user.email,
        "avatar_url": user.avatar_url,
    }


def _public_workflow(workflow: WorkflowRecord, *, include_definition: bool) -> dict[str, Any]:
    result = {
        "id": str(workflow.id),
        "name": workflow.name,
        "description": workflow.description,
        "version": workflow.version,
        "is_active": workflow.is_active,
        "created_at": workflow.created_at.isoformat(),
        "updated_at": workflow.updated_at.isoformat(),
    }
    if include_definition:
        result["definition"] = workflow.definition
        result["webhook_url"] = f"/api/hooks/{workflow.webhook_token}"
    return result


def _public_node_run(node_run: NodeRunRecord) -> dict[str, Any]:
    return {
        "id": str(node_run.id),
        "node_id": node_run.node_id,
        "node_type": node_run.node_type,
        "node_name": node_run.node_name,
        "attempt": node_run.attempt,
        "status": node_run.status,
        "input": node_run.input_data,
        "output": node_run.output_data,
        "error": node_run.error,
        "started_at": node_run.started_at.isoformat() if node_run.started_at else None,
        "finished_at": node_run.finished_at.isoformat() if node_run.finished_at else None,
        "duration_ms": node_run.duration_ms,
    }


@app.get("/health", tags=["health"])
@api.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@api.get("/auth/github/login", tags=["auth"])
def github_login(request: Request) -> RedirectResponse:
    client_id = _required_setting("GITHUB_CLIENT_ID")
    _required_setting("GITHUB_CLIENT_SECRET")
    state = secrets.token_urlsafe(32)
    request.session["oauth_state"] = state
    callback_url = os.getenv(
        "GITHUB_REDIRECT_URI",
        "http://localhost:8000/api/auth/github/callback",
    )
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": callback_url,
            "scope": "read:user user:email",
            "state": state,
        }
    )
    return RedirectResponse(f"https://github.com/login/oauth/authorize?{query}", status_code=302)


@api.get("/auth/github/callback", tags=["auth"])
def github_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    if not code or not state:
        raise HTTPException(status_code=400, detail="GitHub did not return an authorization code and state")
    expected_state = request.session.pop("oauth_state", None)
    if not isinstance(expected_state, str) or not secrets.compare_digest(expected_state, state):
        raise HTTPException(status_code=400, detail="OAuth state validation failed")

    client_id = _required_setting("GITHUB_CLIENT_ID")
    client_secret = _required_setting("GITHUB_CLIENT_SECRET")
    callback_url = os.getenv(
        "GITHUB_REDIRECT_URI",
        "http://localhost:8000/api/auth/github/callback",
    )
    try:
        with httpx.Client(timeout=10) as client:
            token_response = client.post(
                "https://github.com/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                    "redirect_uri": callback_url,
                },
            )
            token_response.raise_for_status()
            token_data = token_response.json()
            access_token = token_data.get("access_token") if isinstance(token_data, dict) else None
            if not isinstance(access_token, str):
                raise HTTPException(status_code=502, detail="GitHub did not return an access token")

            user_response = client.get(
                "https://api.github.com/user",
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {access_token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            user_response.raise_for_status()
            profile = user_response.json()
    except httpx.HTTPError as exc:
        logger.exception("GitHub OAuth exchange failed")
        raise HTTPException(status_code=502, detail="GitHub authentication failed") from exc
    except ValueError as exc:
        logger.exception("GitHub returned invalid OAuth JSON")
        raise HTTPException(status_code=502, detail="GitHub returned an invalid response") from exc

    if (
        not isinstance(profile, dict)
        or not isinstance(profile.get("id"), int)
        or isinstance(profile.get("id"), bool)
        or not isinstance(profile.get("login"), str)
    ):
        raise HTTPException(status_code=502, detail="GitHub returned an invalid user profile")

    user = db.scalar(select(User).where(User.github_id == profile["id"]))
    if user is None:
        user = User(
            github_id=profile["id"],
            login=profile["login"],
            name=profile.get("name"),
            email=profile.get("email"),
            avatar_url=profile.get("avatar_url"),
        )
        db.add(user)
    else:
        user.login = profile["login"]
        user.name = profile.get("name")
        user.email = profile.get("email")
        user.avatar_url = profile.get("avatar_url")
    try:
        db.commit()
        db.refresh(user)
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Could not save GitHub user")
        raise HTTPException(status_code=503, detail="could not save authenticated user") from None

    request.session.clear()
    request.session["user_id"] = str(user.id)
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173")
    return RedirectResponse(frontend_url, status_code=303)


@api.post("/auth/logout", status_code=204, tags=["auth"])
def logout(request: Request) -> Response:
    request.session.clear()
    return Response(status_code=204)


@api.get("/me", tags=["auth"])
def me(user: User = Depends(get_current_user)) -> dict[str, Any]:
    return _public_user(user)


@api.get("/node-types", tags=["workflows"])
def get_node_types() -> list[dict[str, Any]]:
    return node_catalog()


@api.get("/workflows", tags=["workflows"])
def list_workflows(
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    offset = 0
    if cursor is not None:
        try:
            offset = int(cursor)
            if offset < 0:
                raise ValueError
        except ValueError:
            raise HTTPException(status_code=422, detail="cursor must be a non-negative integer") from None
    workflows = db.scalars(
        select(WorkflowRecord)
        .where(WorkflowRecord.user_id == user.id)
        .order_by(WorkflowRecord.updated_at.desc(), WorkflowRecord.id.desc())
        .offset(offset)
        .limit(limit + 1)
    ).all()
    has_more = len(workflows) > limit
    page = workflows[:limit]
    return {
        "items": [_public_workflow(item, include_definition=False) for item in page],
        "next_cursor": str(offset + limit) if has_more else None,
    }


@api.post("/workflows", status_code=201, tags=["workflows"])
def create_workflow(
    definition: dict[str, Any],
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    workflow = parse_workflow(definition)
    record = WorkflowRecord(
        user_id=user.id,
        name=workflow.name,
        description=workflow.description,
        definition=workflow.model_dump(mode="json"),
        schema_version=workflow.schema_version,
        webhook_token=secrets.token_urlsafe(32),
    )
    db.add(record)
    try:
        db.commit()
        db.refresh(record)
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Could not create workflow")
        raise HTTPException(status_code=503, detail="could not save workflow") from None
    return _public_workflow(record, include_definition=True)


@api.get("/workflows/{workflow_id}", tags=["workflows"])
def get_workflow(
    workflow_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    record = db.scalar(
        select(WorkflowRecord).where(
            WorkflowRecord.id == workflow_id,
            WorkflowRecord.user_id == user.id,
        )
    )
    if record is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    return _public_workflow(record, include_definition=True)


@api.put("/workflows/{workflow_id}", tags=["workflows"])
def update_workflow(
    workflow_id: UUID,
    payload: dict[str, Any],
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    definition = dict(payload)
    version = definition.pop("version", None)
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise HTTPException(status_code=422, detail="body must include a positive integer version")
    workflow = parse_workflow(definition)
    record = db.scalar(
        update(WorkflowRecord)
        .where(
            WorkflowRecord.id == workflow_id,
            WorkflowRecord.user_id == user.id,
            WorkflowRecord.version == version,
        )
        .values(
            name=workflow.name,
            description=workflow.description,
            definition=workflow.model_dump(mode="json"),
            schema_version=workflow.schema_version,
            version=WorkflowRecord.version + 1,
            updated_at=func.now(),
        )
        .returning(WorkflowRecord)
    )
    if record is None:
        exists = db.scalar(
            select(WorkflowRecord.id).where(
                WorkflowRecord.id == workflow_id,
                WorkflowRecord.user_id == user.id,
            )
        )
        if exists is None:
            raise HTTPException(status_code=404, detail="workflow not found")
        raise HTTPException(status_code=409, detail="workflow version is stale")
    db.commit()
    db.refresh(record)
    return _public_workflow(record, include_definition=True)


@api.delete("/workflows/{workflow_id}", status_code=204, tags=["workflows"])
def delete_workflow(
    workflow_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    record = db.scalar(
        select(WorkflowRecord).where(
            WorkflowRecord.id == workflow_id,
            WorkflowRecord.user_id == user.id,
        )
    )
    if record is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    db.delete(record)
    db.commit()
    return Response(status_code=204)


@api.post("/workflows/{workflow_id}/run", status_code=202, tags=["executions"])
def run_workflow(
    workflow_id: UUID,
    body: RunRequest | None = Body(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, str]:
    workflow_record = db.scalar(
        select(WorkflowRecord).where(
            WorkflowRecord.id == workflow_id,
            WorkflowRecord.user_id == user.id,
        )
    )
    if workflow_record is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    workflow = parse_workflow(workflow_record.definition)
    trigger = next(node for node in workflow.nodes if NODE_TYPE_SPECS[node.type].is_trigger)
    if trigger.type != NodeType.MANUAL_TRIGGER:
        raise HTTPException(status_code=422, detail="workflow does not have a manual trigger")
    payload_data = body.data if body is not None and body.data is not None else trigger.params.get("sample_data", {})
    if not isinstance(payload_data, dict):
        raise HTTPException(status_code=422, detail="manual trigger data must be an object")
    trigger_payload = TriggerPayload(type=TriggerType.MANUAL, data=payload_data)
    execution = ExecutionRecord(
        workflow_id=workflow_record.id,
        user_id=user.id,
        status="queued",
        trigger_type=trigger_payload.type.value,
        trigger_payload=trigger_payload.model_dump(mode="json"),
        definition_snapshot=workflow.model_dump(mode="json"),
    )
    db.add(execution)
    db.commit()
    db.refresh(execution)

    try:
        celery_app.send_task(
            "app.worker.run_execution",
            args=[str(execution.id), str(user.id)],
        )
    except Exception:
        logger.exception("Could not enqueue execution %s", execution.id)
        execution.status = "failed"
        execution.error = {
            "code": "INTERNAL",
            "message": "execution could not be queued",
            "retryable": False,
            "details": {},
        }
        execution.finished_at = datetime.now(timezone.utc)
        db.commit()
        raise HTTPException(status_code=503, detail="execution could not be queued") from None
    return {"execution_id": str(execution.id)}


@api.get("/workflows/{workflow_id}/executions", tags=["executions"])
def list_workflow_executions(
    workflow_id: UUID,
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    owned_workflow = db.scalar(
        select(WorkflowRecord.id).where(
            WorkflowRecord.id == workflow_id,
            WorkflowRecord.user_id == user.id,
        )
    )
    if owned_workflow is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    executions = db.scalars(
        select(ExecutionRecord)
        .where(
            ExecutionRecord.workflow_id == workflow_id,
            ExecutionRecord.user_id == user.id,
        )
        .order_by(ExecutionRecord.queued_at.desc())
        .limit(limit)
    ).all()
    return {"items": [_public_execution(execution) for execution in executions]}


@api.get("/executions/{execution_id}", tags=["executions"])
def get_execution(
    execution_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    execution = db.scalar(
        select(ExecutionRecord).where(
            ExecutionRecord.id == execution_id,
            ExecutionRecord.user_id == user.id,
        )
    )
    if execution is None:
        raise HTTPException(status_code=404, detail="execution not found")
    node_runs = db.scalars(
        select(NodeRunRecord)
        .join(ExecutionRecord, NodeRunRecord.execution_id == ExecutionRecord.id)
        .where(
            NodeRunRecord.execution_id == execution.id,
            ExecutionRecord.user_id == user.id,
        )
        .order_by(NodeRunRecord.node_id, NodeRunRecord.attempt)
    ).all()
    result = _public_execution(execution)
    result["node_runs"] = [_public_node_run(node_run) for node_run in node_runs]
    return result


def _public_execution(execution: ExecutionRecord) -> dict[str, Any]:
    return {
        "id": str(execution.id),
        "workflow_id": str(execution.workflow_id),
        "status": execution.status,
        "trigger_type": execution.trigger_type,
        "trigger_payload": execution.trigger_payload,
        "error": execution.error,
        "queued_at": execution.queued_at.isoformat(),
        "started_at": execution.started_at.isoformat() if execution.started_at else None,
        "finished_at": execution.finished_at.isoformat() if execution.finished_at else None,
    }


app.include_router(api)
