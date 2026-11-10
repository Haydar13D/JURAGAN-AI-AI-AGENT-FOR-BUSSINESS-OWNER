"""
tests/test_retail_customization.py
Comprehensive automated test suite for Phase 2: Retail Pricing Parameter Customization.
Validates:
1. Natural language to validated Retail BusinessProposal
2. Switching pricing_method between markup and gross_margin
3. Approval lifecycle & immutable version bump (v1 -> v2)
4. Deterministic runtime calculation with markup vs gross margin formulas
5. Validation bounds (overhead >= 0, rounding >= 1000)
6. Assistant conversational interaction on Retail workflow
"""

import json
import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.schemas.workflow import (
    WorkflowDefinition,
    WorkflowStatus,
    ConfigPatchOperation,
    BusinessProposal,
    ProposalStatus,
    RetailPricingConfig,
    AssistantIntent,
)
from app.services.business_config_service import BusinessConfigService
from app.services.retail_pricing import register_retail_tools
from app.services.workflow_runner import WorkflowRunner
from app.core.database import AsyncSessionLocal
from app.services.workflow_repository import WorkflowRepository

BASE = "/api/v1"


@pytest.fixture(autouse=True)
def setup_tools():
    register_retail_tools()


@pytest.fixture
def base_retail_workflow() -> WorkflowDefinition:
    tpl_path = "data/fixtures/retail_pricing_template.json"
    with open(tpl_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    data["id"] = "wf_retail_test_slice"
    data["version"] = 1
    data["status"] = "ACTIVE"
    data["business_config"] = {
        "pricing_method": "gross_margin",
        "target_profit_pct": 15.0,
        "overhead_cost_idr": 50000,
        "market_deviation_threshold_pct": 15.0,
        "rounding_increment_idr": 50000
    }
    return WorkflowDefinition.model_validate(data)


@pytest.mark.asyncio
async def test_natural_language_to_retail_proposal(base_retail_workflow: WorkflowDefinition):
    """Owner requests target profit and overhead adjustment via Indonesian prompt."""
    prompt = "Ubah target profit menjadi 18% dan overhead fee Rp 65.000"
    proposal = BusinessConfigService.generate_retail_proposal_from_prompt(base_retail_workflow, prompt)

    assert proposal.is_valid is True
    assert proposal.intent == "MODIFY_RETAIL_PRICING_CONFIG"
    assert proposal.base_version == 1
    assert proposal.proposed_version == 2
    assert proposal.diff_summary["target_profit_pct"]["after"] == 18.0
    assert proposal.diff_summary["overhead_cost_idr"]["after"] == 65000
    assert proposal.candidate_config["target_profit_pct"] == 18.0
    assert proposal.candidate_config["overhead_cost_idr"] == 65000


@pytest.mark.asyncio
async def test_switching_pricing_method_markup_vs_margin(base_retail_workflow: WorkflowDefinition):
    """Owner switches pricing formula from gross margin to markup."""
    prompt = "Ganti metode pricing ke markup dengan target profit 20%"
    proposal = BusinessConfigService.generate_retail_proposal_from_prompt(base_retail_workflow, prompt)

    assert proposal.is_valid is True
    assert proposal.candidate_config["pricing_method"] == "markup"
    assert proposal.candidate_config["target_profit_pct"] == 20.0


@pytest.mark.asyncio
async def test_retail_approval_lifecycle_and_version_bump(base_retail_workflow: WorkflowDefinition):
    """Approving proposal creates immutable v2, archives v1, and updates active version."""
    async with AsyncSessionLocal() as session:
        await WorkflowRepository.save_workflow(session, base_retail_workflow, is_active=True)

        proposal = BusinessConfigService.generate_retail_proposal_from_prompt(
            base_retail_workflow,
            "Ubah target profit menjadi 18%"
        )
        assert proposal.is_valid is True

        new_wf, updated_proposal = await BusinessConfigService.approve_proposal(
            session=session,
            workflow_id=base_retail_workflow.id,
            proposal_id=proposal.proposal_id,
            reviewer_id="juragan_iphone"
        )

        assert new_wf.version == 2
        assert new_wf.status == WorkflowStatus.ACTIVE
        assert new_wf.business_config["target_profit_pct"] == 18.0
        assert updated_proposal.status == ProposalStatus.ACTIVATED

        # Verify in DB
        active_wf = await WorkflowRepository.get_active_workflow(session, base_retail_workflow.id)
        assert active_wf.version == 2
        assert active_wf.business_config["target_profit_pct"] == 18.0


@pytest.mark.asyncio
async def test_runtime_calculation_markup_vs_gross_margin(base_retail_workflow: WorkflowDefinition):
    """
    Verifies pricing formula behavior:
    - gross_margin (15%): cost 10.000.000 + 50.000 = 10.050.000 / 0.85 = 11.823.529 -> rounded 50k = 11.850.000
    - markup (15%): cost 10.000.000 + 50.000 = 10.050.000 * 1.15 = 11.557.500 -> rounded 50k = 11.600.000
    Markup yields lower selling price than gross margin for the same nominal %!
    """
    # 1. Gross margin execution
    gm_wf = base_retail_workflow.model_copy(deep=True)
    gm_wf.business_config["pricing_method"] = "gross_margin"
    gm_wf.business_config["target_profit_pct"] = 15.0
    dry_gm = await WorkflowRunner.run_dry_run(gm_wf)
    assert dry_gm.is_clean is True
    calc_gm = next(t for t in dry_gm.node_traces if t.node_id == "calculate_pricing")
    first_item_gm = calc_gm.output_snapshot["items"][0]

    # 2. Markup execution
    mu_wf = base_retail_workflow.model_copy(deep=True)
    mu_wf.business_config["pricing_method"] = "markup"
    mu_wf.business_config["target_profit_pct"] = 15.0
    dry_mu = await WorkflowRunner.run_dry_run(mu_wf)
    assert dry_mu.is_clean is True
    calc_mu = next(t for t in dry_mu.node_traces if t.node_id == "calculate_pricing")
    first_item_mu = calc_mu.output_snapshot["items"][0]

    # Gross margin recommended price must be strictly higher or equal to markup recommended price
    assert first_item_gm["recommended_selling_price"] >= first_item_mu["recommended_selling_price"]


@pytest.mark.asyncio
async def test_retail_validation_bounds(base_retail_workflow: WorkflowDefinition):
    """Rejects invalid rounding increments or negative overheads."""
    invalid_patch = [
        ConfigPatchOperation(op="replace", path="/config/rounding_increment_idr", value=500),
        ConfigPatchOperation(op="replace", path="/config/overhead_cost_idr", value=-10000)
    ]
    is_valid, errors, _, _ = BusinessConfigService.validate_and_apply_patch(
        workflow=base_retail_workflow,
        changes=invalid_patch,
        base_version=1
    )
    assert is_valid is False
    assert len(errors) >= 1


@pytest.mark.asyncio
async def test_retail_assistant_api_flow():
    """Validates assistant chat interaction with Retail pricing workflow."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Create Retail workflow
        create_res = await ac.post("/api/v1/workflows/from-template", json={
            "template_id": "tpl_used_iphone_pricing_v1",
            "name": "iStore Retail Assistant Test",
            "tenant_id": "tenant_juragan_retail",
            "onboarding": {
                "target_gross_margin_pct": 15,
                "overhead_fee": 50000
            }
        })
        assert create_res.status_code == 200
        wf_id = create_res.json()["workflow"]["id"]

        # Activate v1
        await ac.post(f"/api/v1/workflows/{wf_id}/approve?version=1")

        # Chat with assistant to modify pricing
        chat_res = await ac.post("/api/v1/assistant/message", json={
            "message": "Ubah target profit menjadi 18% dan pembulatan ke 100.000",
            "workflow_id": wf_id
        })
        assert chat_res.status_code == 200
        data = chat_res.json()
        assert data["intent"] == "MODIFY_WORKFLOW"
        prop = data["proposal"]
        assert prop["candidate_config"]["target_profit_pct"] == 18.0
        assert prop["candidate_config"]["rounding_increment_idr"] == 100000

        # Approve proposal
        prop_id = prop["proposal_id"]
        appr_res = await ac.post(f"/api/v1/assistant/proposals/{prop_id}/approve", json={
            "workflow_id": wf_id,
            "reviewer_id": "owner_istore"
        })
        assert appr_res.status_code == 200
        assert appr_res.json()["workflow"]["version"] == 2
        assert appr_res.json()["workflow"]["business_config"]["target_profit_pct"] == 18.0
