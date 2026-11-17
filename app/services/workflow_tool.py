"""
app/services/workflow_tool.py
T5 — Menjadikan eksekusi Workflow DAG sebagai salah satu tool di ToolRegistry ("workflow.run").
"""

import uuid
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.core.database import AsyncSessionLocal
from app.core.tool_registry import (
    BaseTool,
    ExecutionContext,
    ToolCategory,
    ToolMetadata,
    ToolRegistry,
    ToolRiskTier,
)
from app.schemas.workflow import RunStatus, WorkflowDefinition, WorkflowStatus
from app.services.workflow_repository import WorkflowRepository
from app.services.workflow_runner import WorkflowRunner


class WorkflowRunInput(BaseModel):
    workflow_id: str = Field(..., description="ID workflow yang akan dijalankan")
    version: Optional[int] = Field(default=None, description="Versi spesifik workflow (opsional; default versi ACTIVE)")
    csv_content: Optional[str] = Field(default=None, description="Konten CSV input untuk workflow (opsional)")


class WorkflowRunOutput(BaseModel):
    status: str
    nodes_executed: List[str] = Field(default_factory=list)
    final_output: Any = None
    pending_action: Optional[Dict[str, Any]] = None


class WorkflowRunTool(BaseTool):
    """Menjalankan workflow DAG tersertifikasi yang berstatus ACTIVE sebagai skill/tool."""

    metadata = ToolMetadata(
        tool_type="workflow.run",
        version="1.0.0",
        display_name="Run Certified Workflow",
        description="Menjalankan workflow bisnis tersertifikasi (versi ACTIVE) dan mengembalikan ringkasan hasil eksekusi.",
        category=ToolCategory.ANALYSIS,
        risk_tier=ToolRiskTier.INTERNAL,
        is_side_effect=False,
        requires_action_approval=False,
    )
    input_schema = WorkflowRunInput
    output_schema = WorkflowRunOutput

    async def _load_workflow(
        self, inputs: WorkflowRunInput, context: ExecutionContext
    ) -> Optional[WorkflowDefinition]:
        repo = context.extra_meta.get("workflow_repository") or WorkflowRepository
        db_session = context.extra_meta.get("db_session")

        async def _fetch(sess: Any) -> Optional[WorkflowDefinition]:
            if inputs.version is not None:
                return await repo.get_workflow_version(sess, inputs.workflow_id, inputs.version)
            return await repo.get_active_workflow(sess, inputs.workflow_id)

        if db_session is not None or repo is not WorkflowRepository:
            return await _fetch(db_session)

        async with AsyncSessionLocal() as session:
            return await _fetch(session)

    async def execute(
        self, config: BaseModel, inputs: WorkflowRunInput, context: ExecutionContext
    ) -> WorkflowRunOutput:
        wf = await self._load_workflow(inputs, context)
        if not wf or wf.status != WorkflowStatus.ACTIVE:
            return WorkflowRunOutput(
                status="NO_ACTIVE_VERSION",
                nodes_executed=[],
                final_output=None,
                pending_action=None,
            )

        action_approved = bool(context.extra_meta.get("action_approved", False))
        run_id = context.run_id or f"run_{uuid.uuid4().hex[:8]}"

        run_status, traces, pending_action, final_out = await WorkflowRunner.run_live(
            workflow=wf,
            run_id=run_id,
            csv_content=inputs.csv_content,
            action_approval_granted=action_approved,
            reviewer_id=context.actor_id,
        )

        mapped_status = (
            "APPROVAL_REQUIRED"
            if run_status == RunStatus.WAITING_APPROVAL
            else run_status.value
        )
        final_out_serialized = (
            final_out.model_dump() if isinstance(final_out, BaseModel) else final_out
        )
        pending_serialized = (
            pending_action.model_dump()
            if isinstance(pending_action, BaseModel)
            else pending_action
        )

        return WorkflowRunOutput(
            status=mapped_status,
            nodes_executed=[t.node_id for t in traces],
            final_output=final_out_serialized,
            pending_action=pending_serialized,
        )


def register_workflow_tool() -> None:
    """Mendaftarkan tool workflow.run ke ToolRegistry tunggal."""
    ToolRegistry.register(WorkflowRunTool())


register_workflow_tool()
