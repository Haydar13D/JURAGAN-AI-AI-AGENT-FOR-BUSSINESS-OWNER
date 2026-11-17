"""
tests/test_workflow_tool.py
T5 — Workflow jadi tool, bukan atasan.
Memverifikasi:
1. Metadata workflow.run (tool_type='workflow.run', category=ANALYSIS, risk_tier=INTERNAL).
2. Eksekusi dengan fake repository berisi workflow ACTIVE menjalankan node & mengembalikan ringkasan.
3. Workflow tanpa versi ACTIVE mengembalikan status 'NO_ACTIVE_VERSION' (bukan exception).
4. Runner yang mengembalikan WAITING_APPROVAL dipetakan menjadi status 'APPROVAL_REQUIRED' + pending_action.
"""

import pytest
from pydantic import BaseModel, Field

from app.core.tool_registry import (
    BaseTool,
    ExecutionContext,
    ToolCategory,
    ToolMetadata,
    ToolRegistry,
    ToolRiskTier,
)
from app.schemas.workflow import (
    ActionApprovalRequest,
    RunStatus,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
    WorkflowStatus,
)
from app.services.workflow_runner import WorkflowRunner
from app.services.workflow_tool import (
    WorkflowRunInput,
    WorkflowRunTool,
    register_workflow_tool,
)


class _EchoInput(BaseModel):
    text: str = Field(default="ok")


class _EchoOutput(BaseModel):
    echo: str


class _EchoTool(BaseTool):
    metadata = ToolMetadata(
        tool_type="test.workflow_echo",
        version="1.0.0",
        display_name="Echo",
        description="Dummy tool untuk node workflow.",
        category=ToolCategory.ANALYSIS,
        risk_tier=ToolRiskTier.NONE,
    )
    input_schema = _EchoInput
    output_schema = _EchoOutput

    async def execute(self, config, inputs: _EchoInput, context) -> _EchoOutput:
        return _EchoOutput(echo=inputs.text)


class _FakeRepo:
    def __init__(self, workflows_by_id):
        self.workflows = workflows_by_id

    async def get_active_workflow(self, session, workflow_id: str):
        wf = self.workflows.get(workflow_id)
        if wf and wf.status == WorkflowStatus.ACTIVE:
            return wf
        return None

    async def get_workflow_version(self, session, workflow_id: str, version: int):
        wf = self.workflows.get(workflow_id)
        if wf and wf.version == version:
            return wf
        return None


def _sample_active_workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        id="wf_echo_active",
        tenant_id="tenant_test",
        template_id="tpl_test",
        version=1,
        status=WorkflowStatus.ACTIVE,
        name="Echo Active Workflow",
        description="Workflow uji untuk workflow.run",
        nodes=[
            WorkflowNode(
                id="step_echo",
                tool_type="test.workflow_echo",
                tool_version="1.0.0",
                display_name="Echo Step",
                config={},
                input_bindings={},
                output_schema="_EchoOutput",
            )
        ],
        edges=[],
    )


def test_workflow_tool_metadata_and_registration():
    register_workflow_tool()
    tool = ToolRegistry.get("workflow.run")
    assert tool is not None
    assert tool.metadata.tool_type == "workflow.run"
    assert tool.metadata.category == ToolCategory.ANALYSIS
    assert tool.metadata.risk_tier == ToolRiskTier.INTERNAL


@pytest.mark.asyncio
async def test_workflow_tool_executes_active_workflow():
    ToolRegistry.register(_EchoTool())
    active_wf = _sample_active_workflow()
    fake_repo = _FakeRepo({"wf_echo_active": active_wf})

    tool = WorkflowRunTool()
    ctx = ExecutionContext(
        tenant_id="tenant_test",
        workflow_id="agent",
        workflow_version=1,
        run_id="run_t5_ok",
        extra_meta={"workflow_repository": fake_repo},
    )
    out = await tool.execute(
        tool.validate_config({}),
        WorkflowRunInput(workflow_id="wf_echo_active"),
        ctx,
    )

    assert out.status == "COMPLETED"
    assert out.nodes_executed == ["step_echo"]
    assert out.final_output == {"echo": "ok"}
    assert out.pending_action is None


@pytest.mark.asyncio
async def test_workflow_tool_without_active_version_returns_no_active_version():
    draft_wf = _sample_active_workflow().model_copy(update={"status": WorkflowStatus.DRAFT})
    fake_repo = _FakeRepo({"wf_draft": draft_wf})

    tool = WorkflowRunTool()
    ctx = ExecutionContext(
        tenant_id="tenant_test",
        workflow_id="agent",
        workflow_version=1,
        run_id="run_t5_no_active",
        extra_meta={"workflow_repository": fake_repo},
    )
    out = await tool.execute(
        tool.validate_config({}),
        WorkflowRunInput(workflow_id="wf_draft"),
        ctx,
    )

    assert out.status == "NO_ACTIVE_VERSION"
    assert out.nodes_executed == []
    assert out.final_output is None


@pytest.mark.asyncio
async def test_workflow_tool_maps_waiting_approval_to_approval_required(monkeypatch):
    active_wf = _sample_active_workflow()
    fake_repo = _FakeRepo({"wf_echo_active": active_wf})

    pending_req = ActionApprovalRequest(
        action_id="act_123",
        run_id="run_t5_wait",
        workflow_id="wf_echo_active",
        node_id="gate_1",
        action_type="EXTERNAL_PUBLISH",
        summary="Butuh persetujuan owner",
        preview_data={"target": "ig"},
        created_at="2026-11-17T10:00:00",
    )

    async def _fake_run_live(**kwargs):
        return RunStatus.WAITING_APPROVAL, [], pending_req, None

    monkeypatch.setattr(WorkflowRunner, "run_live", staticmethod(_fake_run_live))

    tool = WorkflowRunTool()
    ctx = ExecutionContext(
        tenant_id="tenant_test",
        workflow_id="agent",
        workflow_version=1,
        run_id="run_t5_wait",
        extra_meta={"workflow_repository": fake_repo},
    )
    out = await tool.execute(
        tool.validate_config({}),
        WorkflowRunInput(workflow_id="wf_echo_active"),
        ctx,
    )

    assert out.status == "APPROVAL_REQUIRED"
    assert out.pending_action is not None
    assert out.pending_action["action_id"] == "act_123"
