"""
tests/test_runner_resume.py
T8 — Checkpoint & resume yang idempoten.
Memverifikasi:
1. Fake tool ingest dengan counter: Run -> berhenti di gate -> approve -> resume -> counter == 1 (bukan 2).
2. Output node sebelumnya dipakai ulang dari checkpoint oleh node downstream (bukan dihitung ulang).
3. Run dengan run_id baru tetap mengeksekusi normal dari awal.
"""

import uuid
import pytest
from pydantic import BaseModel, Field

from app.core.database import init_db
from app.core.tool_registry import (
    BaseTool,
    ToolCategory,
    ToolMetadata,
    ToolRegistry,
    ToolRiskTier,
)
from app.schemas.workflow import (
    RunStatus,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
    WorkflowStatus,
)
from app.services.workflow_runner import WorkflowRunner


class _IngestOut(BaseModel):
    batch_token: str
    rows: int


class _GateIn(BaseModel):
    payload: _IngestOut


class _GateOut(BaseModel):
    approved: bool = True
    payload: _IngestOut


class _PublishIn(BaseModel):
    approved_payload: _GateOut
    idempotency_key: str = "test"


class _PublishOut(BaseModel):
    published_token: str
    rows: int


class _CountingIngestTool(BaseTool):
    metadata = ToolMetadata(
        tool_type="test.resume_ingest",
        version="1.0.0",
        display_name="Counting Ingest",
        description="Ingest dengan counter untuk uji checkpoint.",
        category=ToolCategory.INGESTION,
        risk_tier=ToolRiskTier.NONE,
    )
    output_schema = _IngestOut

    def __init__(self):
        self.calls = 0

    async def execute(self, config, inputs, context) -> _IngestOut:
        self.calls += 1
        return _IngestOut(batch_token=f"batch_{self.calls}_{uuid.uuid4().hex[:6]}", rows=42)


class _ApprovalGateTool(BaseTool):
    metadata = ToolMetadata(
        tool_type="test.resume_gate",
        version="1.0.0",
        display_name="Approval Gate",
        description="Gate approval uji.",
        category=ToolCategory.GOVERNANCE,
        risk_tier=ToolRiskTier.NONE,
        requires_action_approval=True,
    )
    input_schema = _GateIn
    output_schema = _GateOut

    def __init__(self):
        self.calls = 0

    async def execute(self, config, inputs: _GateIn, context) -> _GateOut:
        self.calls += 1
        return _GateOut(approved=True, payload=inputs.payload)


class _PublishTool(BaseTool):
    metadata = ToolMetadata(
        tool_type="test.resume_publish",
        version="1.0.0",
        display_name="Publish Output",
        description="Publish downstream.",
        category=ToolCategory.EXPORT,
        risk_tier=ToolRiskTier.EXTERNAL,
        requires_upstream_gate=True,
    )
    input_schema = _PublishIn
    output_schema = _PublishOut

    def __init__(self):
        self.calls = 0

    async def execute(self, config, inputs: _PublishIn, context) -> _PublishOut:
        self.calls += 1
        return _PublishOut(
            published_token=inputs.approved_payload.payload.batch_token,
            rows=inputs.approved_payload.payload.rows,
        )


def _build_workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        id="wf_resume_test",
        tenant_id="tenant_resume",
        template_id="tpl_resume",
        version=1,
        status=WorkflowStatus.ACTIVE,
        name="Resume Test Workflow",
        description="Uji checkpoint & resume idempoten.",
        nodes=[
            WorkflowNode(
                id="ingest",
                tool_type="test.resume_ingest",
                tool_version="1.0.0",
                display_name="Ingest",
                config={},
                input_bindings={},
                output_schema="_IngestOut",
            ),
            WorkflowNode(
                id="gate",
                tool_type="test.resume_gate",
                tool_version="1.0.0",
                display_name="Gate",
                config={"action_type": "TEST_GATE"},
                input_bindings={"payload": "ingest.output"},
                output_schema="_GateOut",
            ),
            WorkflowNode(
                id="publish",
                tool_type="test.resume_publish",
                tool_version="1.0.0",
                display_name="Publish",
                config={},
                input_bindings={"approved_payload": "gate.output"},
                output_schema="_PublishOut",
            ),
        ],
        edges=[
            WorkflowEdge(source="ingest", target="gate"),
            WorkflowEdge(source="gate", target="publish"),
        ],
    )


@pytest.mark.asyncio
async def test_resume_after_approval_does_not_rerun_completed_nodes():
    await init_db()
    ingest_spy = _CountingIngestTool()
    gate_spy = _ApprovalGateTool()
    publish_spy = _PublishTool()
    ToolRegistry.register(ingest_spy)
    ToolRegistry.register(gate_spy)
    ToolRegistry.register(publish_spy)

    wf = _build_workflow()
    run_id = f"run_resume_{uuid.uuid4().hex[:8]}"

    # Step 1: Run tanpa approval -> ingest jalan 1x, pause di gate
    st1, traces1, pending1, out1 = await WorkflowRunner.run_live(
        workflow=wf,
        run_id=run_id,
        action_approval_granted=False,
    )
    assert st1 == RunStatus.WAITING_APPROVAL
    assert pending1 is not None
    assert out1 is None
    assert ingest_spy.calls == 1
    assert gate_spy.calls == 0
    assert publish_spy.calls == 0

    first_batch_token = traces1[0].output_snapshot["batch_token"]

    # Step 2: Resume dengan approval -> ingest TIDAK dijalankan ulang (tetap 1), gate & publish jalan 1x
    st2, traces2, pending2, out2 = await WorkflowRunner.run_live(
        workflow=wf,
        run_id=run_id,
        action_approval_granted=True,
        reviewer_id="owner_test",
    )
    assert st2 == RunStatus.COMPLETED
    assert pending2 is None
    assert ingest_spy.calls == 1  # Idempoten! Bukan 2
    assert gate_spy.calls == 1
    assert publish_spy.calls == 1
    assert traces2[0].status == RunStatus.COMPLETED
    assert traces2[0].execution_time_ms == 0.0
    # Output node sebelumnya dipakai ulang oleh publish
    assert out2.published_token == first_batch_token
    assert out2.rows == 42


@pytest.mark.asyncio
async def test_new_run_id_executes_fresh_without_reusing_other_run_checkpoint():
    await init_db()
    ingest_spy = _CountingIngestTool()
    gate_spy = _ApprovalGateTool()
    publish_spy = _PublishTool()
    ToolRegistry.register(ingest_spy)
    ToolRegistry.register(gate_spy)
    ToolRegistry.register(publish_spy)

    wf = _build_workflow()
    run_a = f"run_a_{uuid.uuid4().hex[:8]}"
    run_b = f"run_b_{uuid.uuid4().hex[:8]}"

    await WorkflowRunner.run_live(workflow=wf, run_id=run_a, action_approval_granted=False)
    assert ingest_spy.calls == 1

    await WorkflowRunner.run_live(workflow=wf, run_id=run_b, action_approval_granted=False)
    assert ingest_spy.calls == 2
