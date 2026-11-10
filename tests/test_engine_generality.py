"""
Engine generality tests.

These prove the engine (registry + validator + runner) composes tools from any pack
without domain-specific code, and that the approval invariant is enforced from static
risk-tier metadata rather than tool names.
"""

import os
from pathlib import Path

import pytest

from app.core.tool_registry import (
    BaseTool,
    EmptyConfig,
    EmptyInput,
    ExecutionContext,
    ToolCategory,
    ToolMetadata,
    ToolRegistry,
    ToolRiskTier,
)
from app.schemas.workflow import WorkflowDefinition, WorkflowEdge, WorkflowNode
from app.services.fnb_marketing import register_fnb_tools
from app.services.inventory_pack import register_inventory_tools
from app.services.retail_pricing import register_retail_tools
from app.services.workflow_runner import WorkflowRunner
from app.services.workflow_validator import WorkflowValidator

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_LEDGER = REPO_ROOT / "tests" / "fixtures" / "real_umkm" / "dev" / "fixture_02_kartu_stok_bahan.csv"


@pytest.fixture(autouse=True)
def _all_packs_registered():
    register_retail_tools()
    register_fnb_tools()
    register_inventory_tools()
    yield


def _node(node_id: str, tool_type: str, config=None, bindings=None) -> WorkflowNode:
    return WorkflowNode(
        id=node_id,
        tool_type=tool_type,
        config=config or {},
        input_bindings=bindings or {},
        output_schema="Any",
    )


def _wf(nodes, edges, wf_id="wf_test") -> WorkflowDefinition:
    return WorkflowDefinition(
        id=wf_id,
        tenant_id="tenant_test",
        template_id="synthesized",  # not a catalog template: engine must not care
        name="Synthesized workflow",
        nodes=nodes,
        edges=[WorkflowEdge(source=s, target=t) for s, t in edges],
    )


# ---------------------------------------------------------------------
# Risk tiers are static metadata
# ---------------------------------------------------------------------

def test_risk_tier_derived_for_legacy_tools_and_explicit_for_inventory():
    assert ToolRegistry.get("fnb.publish_instagram").metadata.risk_tier == ToolRiskTier.EXTERNAL
    assert ToolRegistry.get("retail.export_catalog").metadata.risk_tier == ToolRiskTier.EXTERNAL
    assert ToolRegistry.get("fnb.plan_campaign").metadata.risk_tier == ToolRiskTier.NONE
    # Draft-only: Kirata never sends, the owner does.
    assert ToolRegistry.get("inventory.draft_supplier_po").metadata.risk_tier == ToolRiskTier.INTERNAL


# ---------------------------------------------------------------------
# Approval invariant: gate must be UPSTREAM of every gated node
# ---------------------------------------------------------------------

def _fnb_chain(gate_position: str) -> WorkflowDefinition:
    nodes = [
        _node("ingest", "fnb.ingest_pos_data"),
        _node("gate", "governance.action_approval"),
        _node("publish", "fnb.publish_instagram"),
    ]
    if gate_position == "before":
        edges = [("ingest", "gate"), ("gate", "publish")]
    else:  # gate exists but sits after the risky node
        edges = [("ingest", "publish"), ("publish", "gate")]
    return _wf(nodes, edges)


def test_gate_before_external_node_is_valid():
    res = WorkflowValidator.validate_workflow(_fnb_chain("before"))
    assert not any("Safety Violation" in e for e in res.errors), res.errors


def test_gate_after_external_node_is_rejected():
    """The old validator only checked that a gate existed somewhere. That let this through."""
    res = WorkflowValidator.validate_workflow(_fnb_chain("after"))
    assert not res.is_valid
    assert any("Safety Violation" in e and "'publish'" in e for e in res.errors)


class _FakeFinancialTool(BaseTool):
    """Tool whose name gives no hint of risk. Only the static tier marks it as gated."""
    metadata = ToolMetadata(
        tool_type="test.create_invoice",
        display_name="Create invoice",
        description="test-only",
        category=ToolCategory.TRANSFORMATION,
        risk_tier=ToolRiskTier.FINANCIAL,
    )
    config_schema = EmptyConfig
    input_schema = EmptyInput

    async def execute(self, config, inputs, context: ExecutionContext):
        return EmptyInput()


def test_financial_tier_is_gated_even_without_risky_name():
    ToolRegistry.register(_FakeFinancialTool())
    try:
        wf = _wf([_node("ingest", "inventory.ingest_stock_ledger"), _node("inv", "test.create_invoice")],
                 [("ingest", "inv")])
        res = WorkflowValidator.validate_workflow(wf)
        assert any("Safety Violation" in e and "'inv'" in e for e in res.errors)
    finally:
        ToolRegistry._registry.pop("test.create_invoice", None)


def test_ancestor_map_is_complete_in_diamond_graphs():
    """Regression: the old memoized ancestor map could cache an incomplete set in diamonds."""
    nodes = [_node(n, "inventory.ingest_stock_ledger") for n in ("A", "X", "B", "C", "D")]
    edges = [("A", "B"), ("A", "C"), ("X", "C"), ("B", "D"), ("C", "D")]
    ancestors = WorkflowValidator._build_ancestor_map(nodes, [WorkflowEdge(source=s, target=t) for s, t in edges])
    assert ancestors["C"] == {"A", "X"}
    assert ancestors["D"] == {"A", "B", "C", "X"}


# ---------------------------------------------------------------------
# A non-template, hand-composed DAG runs end-to-end on real data
# ---------------------------------------------------------------------

def _inventory_workflow() -> WorkflowDefinition:
    nodes = [
        _node("baca_stok", "inventory.ingest_stock_ledger"),
        _node("cek_batas", "inventory.filter_low_stock",
              config={"default_safety_stock": 100.0},
              bindings={"snapshot": "baca_stok.output"}),
        _node("draf_po", "inventory.draft_supplier_po",
              config={
                  "store_name": "Kedai Burger Juara",
                  "suppliers": {"SUP-ROTI": {"name": "Pabrik Roti Mas Joko", "phone": "0812-3456-7890",
                                             "items": ["Roti bun burger"]}},
                  "alias_table": {"Roti bun (burger)": "Roti bun burger"},
              },
              bindings={"report": "cek_batas.output"}),
    ]
    return _wf(nodes, [("baca_stok", "cek_batas"), ("cek_batas", "draf_po")], wf_id="wf_inventory")


def test_inventory_workflow_validates_without_approval_gate():
    """INTERNAL-tier drafts do not need a gate; the validator should not over-block."""
    res = WorkflowValidator.validate_workflow(_inventory_workflow())
    assert res.is_valid, res.errors


@pytest.mark.asyncio
async def test_inventory_workflow_dry_runs_on_real_fixture():
    csv_text = FIXTURE_LEDGER.read_text(encoding="utf-8")
    result = await WorkflowRunner.run_dry_run(_inventory_workflow(), custom_csv_content=csv_text)

    assert result.is_clean, result.summary
    assert [t.node_id for t in result.node_traces] == ["baca_stok", "cek_batas", "draf_po"]

    out = result.node_traces[-1].output_snapshot
    # Only 'Roti bun' (83 pcs) is below 100; it maps to the bakery supplier via the alias table.
    assert len(out["drafts"]) == 1
    draft = out["drafts"][0]
    assert draft["supplier_phone"] == "6281234567890"
    assert "Roti bun (burger): *17 pcs*" in draft["po_text"]
    assert draft["status"] == "Draf diserahkan ke owner"
    assert out["unassigned_items"] == []


# ---------------------------------------------------------------------
# Generality guard: engine code must not branch on specific templates/tools
# ---------------------------------------------------------------------

def _engine_source(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def test_validator_has_no_template_or_tool_specific_branches():
    src = _engine_source("app/services/workflow_validator.py")
    assert "template_id ==" not in src
    assert "tool_type ==" not in src


def test_runner_has_no_template_or_tool_specific_branches():
    src = _engine_source("app/services/workflow_runner.py")
    assert "template_id ==" not in src
    assert "tool_type ==" not in src

