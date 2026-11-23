import os
import asyncio
import logging
from datetime import datetime, timezone
from uuid import UUID

from celery import Celery
from sqlalchemy import select, update

from app.contracts import ErrorCode, ExecutionStatus, NodeRunStatus, TriggerPayload, parse_workflow
from app.database import ExecutionRecord, NodeRunRecord, SessionLocal
from app.engine import WorkflowEngine

logger = logging.getLogger("flowbridge.worker")

celery_app = Celery(
    "flowbridge",
    broker=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
)
celery_app.conf.update(
    task_default_queue="flowbridge",
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)


@celery_app.task(name="app.worker.run_execution")
def run_execution(execution_id: str, user_id: str) -> dict[str, str]:
    workflow = None
    try:
        parsed_id = UUID(execution_id)
        parsed_user_id = UUID(user_id)
    except ValueError:
        logger.error("Ignoring execution task with invalid execution or user UUID")
        return {"status": "invalid"}

    with SessionLocal() as db:
        execution = db.scalar(
            update(ExecutionRecord)
            .where(
                ExecutionRecord.id == parsed_id,
                ExecutionRecord.user_id == parsed_user_id,
                ExecutionRecord.status == "queued",
            )
            .values(status="running", started_at=datetime.now(timezone.utc))
            .returning(ExecutionRecord)
        )
        if execution is None:
            return {"status": "ignored"}
        definition = execution.definition_snapshot
        trigger_payload = execution.trigger_payload
        workflow_id = str(execution.workflow_id)
        user_id = str(execution.user_id)
        db.commit()

    try:
        workflow = parse_workflow(definition)
        run_result = asyncio.run(
            WorkflowEngine().run(
                workflow,
                TriggerPayload.model_validate(trigger_payload),
                execution_id=execution_id,
                workflow_id=workflow_id,
                user_id=user_id,
            )
        )
        final_status = run_result.status.value
        node_results = run_result.node_results
        attempt_results = run_result.attempt_results
        node_inputs = run_result.node_inputs
        node_statuses = run_result.node_statuses
    except Exception:
        logger.exception("Execution %s failed before the engine returned a result", execution_id)
        final_status = ExecutionStatus.FAILED.value
        node_results = {}
        attempt_results = {}
        node_inputs = {}
        node_statuses = {}
        execution_error = {
            "code": ErrorCode.INTERNAL.value,
            "message": "execution failed unexpectedly",
            "retryable": False,
            "details": {},
        }
    else:
        failed_node = next(
            (
                node_result
                for node_result in node_results.values()
                if node_result.status == "failed"
            ),
            None,
        )
        execution_error = (
            failed_node.error.model_dump(mode="json")
            if final_status == ExecutionStatus.FAILED.value and failed_node and failed_node.error
            else None
        )

    node_by_id = {node.id: node for node in workflow.nodes} if workflow is not None else {}
    with SessionLocal() as db:
        execution_record = db.scalar(
            select(ExecutionRecord).where(
                ExecutionRecord.id == parsed_id,
                ExecutionRecord.user_id == parsed_user_id,
            )
        )
        if execution_record is None:
            logger.error("Execution %s disappeared while running", execution_id)
            return {"status": "missing"}

        finished_at = datetime.now(timezone.utc)
        for node_id, node in node_by_id.items():
            status = node_statuses.get(node_id, NodeRunStatus.SKIPPED)
            input_envelope = node_inputs.get(node_id)
            input_data = input_envelope.model_dump(mode="json") if input_envelope else None
            node_attempts = attempt_results.get(node_id, [])
            if not node_attempts:
                db.add(
                    NodeRunRecord(
                        execution_id=parsed_id,
                        node_id=node.id,
                        node_type=node.type.value,
                        node_name=node.name,
                        attempt=1,
                        status=status.value,
                        input_data=input_data,
                    )
                )
                continue
            for attempt, node_result in enumerate(node_attempts, start=1):
                db.add(
                    NodeRunRecord(
                        execution_id=parsed_id,
                        node_id=node.id,
                        node_type=node.type.value,
                        node_name=node.name,
                        attempt=attempt,
                        status=(
                            NodeRunStatus.SUCCESS.value
                            if node_result.status == "success"
                            else NodeRunStatus.FAILED.value
                        ),
                        input_data=input_data,
                        output_data=(
                            {
                                handle: envelope.model_dump(mode="json")
                                for handle, envelope in node_result.outputs.items()
                            }
                            if node_result.status == "success"
                            else None
                        ),
                        error=(
                            node_result.error.model_dump(mode="json")
                            if node_result.error is not None
                            else None
                        ),
                    )
                )

        execution_record.status = final_status
        execution_record.error = execution_error
        execution_record.finished_at = finished_at
        db.commit()

    return {"status": final_status}
