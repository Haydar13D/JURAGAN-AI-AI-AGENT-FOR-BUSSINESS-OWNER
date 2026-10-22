"""
tests/test_fnb_workflow.py
Comprehensive automated test suite for the F&B Sales-to-Marketing Canonical Template (FR-16).
Validates template loading, DAG validation, dry-run, two-tier approval, and AI patch proposal.
"""

import os
import json
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.schemas.workflow import WorkflowDefinition, RunStatus, WorkflowStatus
from app.core.tool_registry import ToolRegistry
from app.services.fnb_marketing import register_fnb_tools
from app.services.workflow_validator import WorkflowValidator
from app.services.workflow_runner import WorkflowRunner
from app.services.proposal_generator import ProposalGenerator

BASE = "/api/v1/workflows"


@pytest.fixture(autouse=True)
def setup_fnb_ecosystem():
    register_fnb_tools()


@pytest.fixture
def fnb_workflow() -> WorkflowDefinition:
    tpl_path = "data/fixtures/fnb_marketing_template.json"
    with open(tpl_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return WorkflowDefinition.model_validate(data)


def test_fnb_template_loading_and_dag_validation(fnb_workflow: WorkflowDefinition):
    assert fnb_workflow.id == "tpl_fnb_sales_to_marketing_v1"
    assert len(fnb_workflow.nodes) == 6
    assert len(fnb_workflow.edges) == 5

    res = WorkflowValidator.validate_workflow(fnb_workflow, enforce_tool_allowlist=True)
    assert res.is_valid is True
    assert len(res.errors) == 0


@pytest.mark.asyncio
async def test_fnb_dry_run_execution(fnb_workflow: WorkflowDefinition):
    dry = await WorkflowRunner.run_dry_run(fnb_workflow)
    assert dry.is_clean is True
    assert len(dry.node_traces) == 6
    assert all(t.status == RunStatus.COMPLETED for t in dry.node_traces)


@pytest.mark.asyncio
async def test_fnb_live_run_action_approval_lifecycle(fnb_workflow: WorkflowDefinition):
    # Step 1: Live run without action approval -> MUST PAUSE at owner_action_approval
    status, traces, pending_act, out = await WorkflowRunner.run_live(
        workflow=fnb_workflow,
        run_id="run_fnb_test_001",
        action_approval_granted=False
    )
    assert status == RunStatus.WAITING_APPROVAL
    assert pending_act is not None
    assert pending_act.action_type == "CAMPAIGN_PUBLISH_OR_POST"
    assert out is None  # Instagram publishing blocked!

    # Step 2: Live run with explicit human approval -> PROCEEDS and PUBLISHES
    status_appr, traces_appr, pending_appr, out_appr = await WorkflowRunner.run_live(
        workflow=fnb_workflow,
        run_id="run_fnb_test_001",
        action_approval_granted=True,
        reviewer_id="owner_cafe"
    )
    assert status_appr == RunStatus.COMPLETED
    assert pending_appr is None
    assert out_appr is not None
    assert out_appr.status in ["MOCK_PUBLISHED", "PUBLISHED"]
    assert "instagram" in out_appr.platform


@pytest.mark.asyncio
async def test_fnb_ai_proposal_patch(fnb_workflow: WorkflowDefinition):
    prop = await ProposalGenerator.generate_proposal(
        base_workflow=fnb_workflow,
        user_prompt="Tolong ubah diskon promo jadi 20% dan penurunan minimal 30%."
    )
    assert prop.is_valid is True
    assert prop.diff is not None
    assert len(prop.patch_operations) >= 1
    assert prop.proposed_version == fnb_workflow.version + 1


@pytest.mark.asyncio
async def test_fnb_api_full_journey():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Create F&B workflow from template with custom onboarding
        r = await ac.post(f"{BASE}/from-template", json={
            "template_id": "tpl_fnb_sales_to_marketing_v1",
            "name": "Kopi Senja Marketing",
            "onboarding": {
                "min_drop_percentage": 20,
                "default_discount_pct": 18,
                "promo_duration_days": 10
            }
        })
        assert r.status_code == 200
        wf = r.json()["workflow"]
        wf_id = wf["id"]
        assert wf["status"] == "DRAFT"

        # Check configured onboarding parameters applied
        plan_node = next(n for n in wf["nodes"] if n["id"] == "plan_campaign")
        assert plan_node["config"]["default_discount_pct"] == 18
        assert plan_node["config"]["promo_duration_days"] == 10

        # Dry run + Approve v1
        dry = (await ac.post(f"{BASE}/{wf_id}/dry-run?version=1")).json()
        assert dry["is_clean"] is True
        appr = await ac.post(f"{BASE}/{wf_id}/approve?version=1")
        assert appr.status_code == 200

        # Live run -> WAITING_APPROVAL
        run = (await ac.post(f"{BASE}/{wf_id}/runs", json={})).json()
        assert run["status"] == "WAITING_APPROVAL"
        act_id = run["pending_action"]["action_id"]

        # Approve action -> COMPLETED
        dec = (await ac.post(f"{BASE}/runs/{run['run_id']}/actions/{act_id}/decision", json={
            "decision": "APPROVE",
            "reviewer_user_id": "juragan_fnb"
        })).json()
        assert dec["status"] == "APPROVED_AND_COMPLETED"
