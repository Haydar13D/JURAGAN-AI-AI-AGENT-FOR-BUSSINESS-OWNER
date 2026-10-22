"""
tests/test_kirata_workflow.py
Comprehensive automated test suite for Kirata AI Workflow Builder.
Validates PRD v2.0 acceptance criteria, deterministic pricing, DAG validation, and approvals.
"""

import os
import json
import pytest
from app.schemas.workflow import (
    WorkflowDefinition,
    WorkflowNode,
    WorkflowEdge,
    PatchOperation,
    PatchOpType,
    RunStatus,
    WorkflowStatus
)
from app.core.tool_registry import ToolRegistry
from app.services.retail_pricing import register_retail_tools
from app.services.workflow_validator import WorkflowValidator
from app.services.patch_engine import PatchEngine
from app.services.workflow_runner import WorkflowRunner
from app.services.proposal_generator import ProposalGenerator


@pytest.fixture(autouse=True)
def setup_retail_ecosystem():
    register_retail_tools()


@pytest.fixture
def base_workflow() -> WorkflowDefinition:
    tpl_path = "data/fixtures/retail_pricing_template.json"
    with open(tpl_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return WorkflowDefinition.model_validate(data)


# =====================================================================
# 1. SCHEMA & TEMPLATE VALIDATION
# =====================================================================

def test_template_loading_and_pydantic_schema(base_workflow: WorkflowDefinition):
    assert base_workflow.id == "tpl_used_iphone_pricing_v1"
    assert len(base_workflow.nodes) == 9
    assert len(base_workflow.edges) == 8
    res = WorkflowValidator.validate_workflow(base_workflow, enforce_tool_allowlist=True)
    assert res.is_valid is True
    assert len(res.errors) == 0


# =====================================================================
# 2. DAG & SAFETY GUARDRAIL CHECKS
# =====================================================================

def test_dag_cycle_detection_rejection(base_workflow: WorkflowDefinition):
    # Add a circular edge: export_catalog -> extract_and_normalize
    bad_edge = WorkflowEdge(source="export_catalog", target="extract_and_normalize")
    base_workflow.edges.append(bad_edge)

    res = WorkflowValidator.validate_workflow(base_workflow, enforce_tool_allowlist=False)
    assert res.is_valid is False
    assert any("cycle detected" in err.lower() for err in res.errors)


def test_unapproved_tool_rejection(base_workflow: WorkflowDefinition):
    # Inject unknown tool
    rogue_node = WorkflowNode(
        id="rogue_scraper",
        tool_type="external.unreviewed_python_eval",
        output_schema="RawText"
    )
    base_workflow.nodes.append(rogue_node)

    res = WorkflowValidator.validate_workflow(base_workflow, enforce_tool_allowlist=True)
    assert res.is_valid is False
    assert any("unapproved or unknown tool" in err.lower() for err in res.errors)


def test_missing_approval_gate_safety_violation(base_workflow: WorkflowDefinition):
    # Remove the owner_action_approval node and connect directly to export
    base_workflow.nodes = [n for n in base_workflow.nodes if n.id != "owner_action_approval"]
    base_workflow.edges = [
        e for e in base_workflow.edges
        if e.source != "owner_action_approval" and e.target != "owner_action_approval"
    ]
    base_workflow.edges.append(WorkflowEdge(source="conditional_review_flag", target="export_catalog"))

    res = WorkflowValidator.validate_workflow(base_workflow, enforce_tool_allowlist=True)
    assert res.is_valid is False
    assert any("safety violation" in err.lower() for err in res.errors)


# =====================================================================
# 3. PATCH ENGINE & VISUAL DIFF
# =====================================================================

def test_patch_engine_margin_update(base_workflow: WorkflowDefinition):
    ops = [
        PatchOperation(
            op=PatchOpType.UPDATE_NODE_CONFIG,
            node_id="calculate_pricing",
            config_patch={"target_gross_margin": 0.20, "overhead_fee": 100000}
        )
    ]
    patched_wf, diff = PatchEngine.apply_patch(
        base_workflow=base_workflow,
        operations=ops,
        new_version=2,
        rationale="Update margin to 20%"
    )

    assert patched_wf.version == 2
    assert patched_wf.status == WorkflowStatus.DRAFT
    # Check node config updated
    calc_node = next(n for n in patched_wf.nodes if n.id == "calculate_pricing")
    assert calc_node.config["target_gross_margin"] == 0.20
    assert calc_node.config["overhead_fee"] == 100000

    # Check visual diff
    diff_node = next(dn for dn in diff.nodes if dn.node_id == "calculate_pricing")
    assert diff_node.status == "MODIFIED"
    assert diff_node.config_changes["target_gross_margin"]["before"] == 0.15
    assert diff_node.config_changes["target_gross_margin"]["after"] == 0.20


# =====================================================================
# 4. DETERMINISTIC PRICING MATH TEST (PRD SECTION 7.3)
# =====================================================================

def test_deterministic_pricing_math():
    """
    PRD Formula:
    Total Cost = Cost (Rp4,500,000) + Overhead (Rp50,000) = Rp4,550,000
    Minimum pre-rounding price = 4,550,000 / (1 - 0.15) = 5,352,941.176
    With Rp50,000 round up increment = Rp5,400,000
    """
    vendor_cost = 4500000
    overhead = 50000
    margin = 0.15
    increment = 50000

    total_cost = vendor_cost + overhead
    raw_min_price = total_cost / (1.0 - margin)

    import math
    recommended = int(math.ceil(raw_min_price / increment) * increment)

    actual_margin = (recommended - total_cost) / recommended
    markup = (recommended - total_cost) / total_cost

    assert total_cost == 4550000
    assert round(raw_min_price, 2) == 5352941.18
    assert recommended == 5400000
    assert actual_margin > margin  # due to upward rounding
    assert actual_margin != markup  # markup and margin are distinct


# =====================================================================
# 5. DRY-RUN SIMULATION
# =====================================================================

@pytest.mark.asyncio
async def test_dry_run_execution(base_workflow: WorkflowDefinition):
    dry_run = await WorkflowRunner.run_dry_run(base_workflow)

    assert dry_run.is_clean is True
    assert len(dry_run.node_traces) == 9
    assert dry_run.items_processed == 25
    assert dry_run.items_flagged_for_review > 0
    assert all(t.status == RunStatus.COMPLETED for t in dry_run.node_traces)


# =====================================================================
# 6. TWO-TIER HUMAN APPROVAL IN LIVE RUN
# =====================================================================

@pytest.mark.asyncio
async def test_two_tier_action_approval_lifecycle(base_workflow: WorkflowDefinition):
    # Step 1: Live run without action approval -> MUST PAUSE at owner_action_approval
    status, traces, pending_act, out = await WorkflowRunner.run_live(
        workflow=base_workflow,
        run_id="run_test_approval_001",
        action_approval_granted=False
    )

    assert status == RunStatus.WAITING_APPROVAL
    assert pending_act is not None
    assert pending_act.action_type == "CATALOG_PUBLISH_OR_EXPORT"
    assert out is None  # Export did not run! Zero unapproved mutations!

    # Step 2: Live run with explicit human approval -> PROCEEDS and EXPORTS
    status_appr, traces_appr, pending_appr, out_appr = await WorkflowRunner.run_live(
        workflow=base_workflow,
        run_id="run_test_approval_001",
        action_approval_granted=True,
        reviewer_id="retail_owner_01"
    )

    assert status_appr == RunStatus.COMPLETED
    assert pending_appr is None
    assert out_appr is not None
    assert out_appr.exported_rows_count > 0
    assert "iPhone" in out_appr.export_csv_content


# =====================================================================
# 7. AI PROPOSAL GENERATOR & UNSUPPORTED CAPABILITIES
# =====================================================================

@pytest.mark.asyncio
async def test_ai_proposal_supported_request(base_workflow: WorkflowDefinition):
    prop = await ProposalGenerator.generate_proposal(
        base_workflow=base_workflow,
        user_prompt="Tolong ubah target margin menjadi 18% dan batre dibawah 80% ditandai review."
    )

    assert prop.is_valid is True
    assert prop.diff is not None
    assert len(prop.patch_operations) >= 1
    assert prop.proposed_version == base_workflow.version + 1


@pytest.mark.asyncio
async def test_ai_proposal_unsupported_marketplace_rejection(base_workflow: WorkflowDefinition):
    prop = await ProposalGenerator.generate_proposal(
        base_workflow=base_workflow,
        user_prompt="Tolong scraping otomatis marketplace Tokopedia dan Shopee untuk ambil harga live."
    )

    assert prop.requires_clarification is True
    assert prop.is_valid is False
    assert len(prop.patch_operations) == 0
    msg = prop.clarification_message.lower()
    assert "belum didukung" in msg or "tidak didukung" in msg
