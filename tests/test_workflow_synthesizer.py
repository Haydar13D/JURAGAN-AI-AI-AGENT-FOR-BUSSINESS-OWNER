"""
Tests for Milestone 2: General Problem-to-Workflow Synthesizer (Evaluator-Optimizer Loop).

Verifies:
1. Cross-domain synthesis from the same ToolRegistry (Inventory Pack vs Marketing Pack vs Retail Pack).
2. Evaluator-Optimizer self-correction: when a draft omits a required approval gate before an EXTERNAL node,
   the validator catches it on Iteration 1 and the optimizer splices the gate in on Iteration 2.
3. Honest out-of-domain rejection (e.g., tax/payroll/crypto/marketplace auto-reply) with feature backlog logging.
4. Dry-run semantic sanity warnings when a filter produces 0% or 100% of rows.
5. Engine generality: zero hardcoded template_id branches inside workflow_synthesizer.py.
"""

from pathlib import Path
from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.services.fnb_marketing import register_fnb_tools
from app.services.inventory_pack import register_inventory_tools
from app.services.retail_pricing import register_retail_tools
from app.services.workflow_synthesizer import WorkflowSynthesizerService

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_LEDGER = REPO_ROOT / "tests" / "fixtures" / "real_umkm" / "dev" / "fixture_02_kartu_stok_bahan.csv"


@pytest.fixture(autouse=True)
def _register_all_packs():
    register_retail_tools()
    register_fnb_tools()
    register_inventory_tools()
    yield


@pytest.mark.asyncio
async def test_synthesize_inventory_workflow_and_dry_run_on_real_fixture():
    """Synthesizer builds an inventory restock DAG from a natural language complaint and runs dry-run."""
    csv_text = FIXTURE_LEDGER.read_text(encoding="utf-8")
    card = await WorkflowSynthesizerService.synthesize_from_problem(
        tenant_id="tenant_burger_01",
        user_problem="Tolong cek kartu stok bahan baku saya, kalau ada yang di bawah 100 pcs siapkan draf pesanan ke supplier",
        sample_csv_content=csv_text,
    )

    assert card.is_supported is True
    assert card.validation is not None and card.validation.is_valid is True
    assert card.workflow is not None
    tool_types = [n.tool_type for n in card.workflow.nodes]
    assert tool_types == [
        "inventory.ingest_stock_ledger",
        "inventory.filter_low_stock",
        "inventory.draft_supplier_po",
    ]
    # Threshold 100 extracted from prompt
    assert card.workflow.nodes[1].config["default_safety_stock"] == 100.0

    # Dry-run executed against real fixture
    assert card.dry_run is not None
    assert card.dry_run.is_clean is True
    assert len(card.sanity_warnings) == 0  # 1 of 5 items flagged (neither 0% nor 100%)

    # Column mappings generated with sample values and confirmation required
    assert card.requires_mapping_confirmation is True
    assert card.schema_signature is not None
    assert any(m.target_field == "current_stock" and "915.0" in m.confirmation_prompt for m in card.column_mappings)


@pytest.mark.asyncio
async def test_synthesize_fnb_marketing_workflow_from_same_registry():
    """Synthesizer builds a marketing promo DAG with approval gate when given a sales-drop problem."""
    card = await WorkflowSynthesizerService.synthesize_from_problem(
        tenant_id="tenant_cafe_01",
        user_problem="Penjualan menu kopi minggu ini turun drastis, tolong buatkan kampanye promo diskon 20% dan flyer instagram",
    )

    assert card.is_supported is True
    assert card.validation is not None and card.validation.is_valid is True
    tool_types = [n.tool_type for n in card.workflow.nodes]
    assert "fnb.ingest_pos_data" in tool_types
    assert "fnb.plan_campaign" in tool_types
    assert "governance.action_approval" in tool_types
    assert "fnb.publish_instagram" in tool_types

    plan_node = next(n for n in card.workflow.nodes if n.tool_type == "fnb.plan_campaign")
    assert plan_node.config["default_discount_pct"] == 20.0


@pytest.mark.asyncio
async def test_evaluator_optimizer_repairs_missing_approval_gate():
    """If an initial draft forgets the approval gate before an EXTERNAL node, the loop repairs it in Iteration 2."""

    def _flawed_generator(problem, catalog, feedback):
        available_types = {c["tool_type"]: c for c in catalog}
        # Iteration 1: returns a DAG without governance.action_approval before fnb.publish_instagram
        nodes = [
            {"id": "ingest_pos", "tool_type": "fnb.ingest_pos_data", "config": {}, "input_bindings": {}},
            {"id": "detect_opportunities", "tool_type": "fnb.detect_opportunities", "config": {}, "input_bindings": {"pos_data": "ingest_pos.output"}},
            {"id": "plan_campaign", "tool_type": "fnb.plan_campaign", "config": {}, "input_bindings": {"opportunities": "detect_opportunities.output"}},
            {"id": "generate_flyer", "tool_type": "fnb.generate_flyer", "config": {}, "input_bindings": {"campaign_plans": "plan_campaign.output"}},
            {"id": "publish_instagram", "tool_type": "fnb.publish_instagram", "config": {}, "input_bindings": {"approved_payload": "generate_flyer.output"}},
        ]
        edges = [
            {"source": "ingest_pos", "target": "detect_opportunities"},
            {"source": "detect_opportunities", "target": "plan_campaign"},
            {"source": "plan_campaign", "target": "generate_flyer"},
            {"source": "generate_flyer", "target": "publish_instagram"},
        ]
        # On Iteration 2, feedback contains the Safety Violation from WorkflowValidator
        repaired_nodes = WorkflowSynthesizerService._auto_insert_approval_gates_if_needed(
            nodes, edges, available_types, feedback
        )
        return {
            "is_supported": True,
            "name": "Repaired Marketing DAG",
            "rationale": "Auto-repaired with approval gate.",
            "nodes": repaired_nodes,
            "edges": edges,
        }

    card = await WorkflowSynthesizerService.synthesize_from_problem(
        tenant_id="tenant_repair",
        user_problem="Buat promo instagram",
        custom_candidate_generator=_flawed_generator,
    )

    assert card.is_supported is True
    assert card.iterations_used == 2  # Iteration 1 failed validation, Iteration 2 repaired it!
    assert card.validation is not None and card.validation.is_valid is True
    assert any(n.tool_type == "governance.action_approval" for n in card.workflow.nodes)


@pytest.mark.asyncio
async def test_honest_rejection_for_out_of_domain_prompts():
    """Synthesizer honestly rejects problems outside registered tools and logs the feature gap."""
    card = await WorkflowSynthesizerService.synthesize_from_problem(
        tenant_id="tenant_tax",
        user_problem="Tolong hitung pajak SPT tahunan dan potong gaji BPJS karyawan otomatis",
    )

    assert card.is_supported is False
    assert card.workflow is None
    assert card.rejection_reason is not None
    assert "belum memiliki" in card.rejection_reason.lower()
    assert card.logged_feature_gap == "Manajemen Pajak / Payroll HR"


@pytest.mark.asyncio
async def test_dry_run_semantic_sanity_warning_on_zero_or_all_rows_filtered():
    """When filter threshold causes 0 rows (or 100% rows) to match, a SanityWarning is raised."""
    csv_text = FIXTURE_LEDGER.read_text(encoding="utf-8")

    # Case A: threshold = 5 pcs -> 0 items in fixture_02 are <= 5 (lowest is Roti bun at 83 pcs)
    card_zero = await WorkflowSynthesizerService.synthesize_from_problem(
        tenant_id="tenant_sanity",
        user_problem="Cek stok bahan baku yang di bawah 5 pcs untuk dipesan ke supplier",
        sample_csv_content=csv_text,
    )
    assert card_zero.is_supported is True
    assert any(w.code == "EMPTY_FILTER_OUTPUT" for w in card_zero.sanity_warnings)

    # Case B: threshold = 5000 pcs -> 100% of items (5/5) in fixture_02 are <= 5000
    card_all = await WorkflowSynthesizerService.synthesize_from_problem(
        tenant_id="tenant_sanity",
        user_problem="Cek stok bahan baku yang di bawah 5000 pcs untuk dipesan ke supplier",
        sample_csv_content=csv_text,
    )
    assert card_all.is_supported is True
    assert any(w.code == "ALL_ROWS_PASSED_FILTER" for w in card_all.sanity_warnings)


def test_synthesizer_has_no_template_id_branches():
    src = (REPO_ROOT / "app" / "services" / "workflow_synthesizer.py").read_text(encoding="utf-8")
    assert "template_id ==" not in src


def test_synthesize_api_endpoint_end_to_end():
    """Verify POST /api/v1/workflows/synthesize returns a Proposal Card and stores the draft workflow."""
    client = TestClient(app)
    csv_text = FIXTURE_LEDGER.read_text(encoding="utf-8")

    resp = client.post(
        "/api/v1/workflows/synthesize",
        json={
            "tenant_id": "tenant_api_test",
            "user_problem": "Cek stok bahan baku burger yang di bawah 100 pcs dan buat draf PO ke supplier",
            "sample_csv_content": csv_text,
            "save_as_draft": True,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_supported"] is True
    assert data["validation"]["is_valid"] is True
    assert data["dry_run"]["is_clean"] is True
    wf_id = data["workflow"]["id"]

    # Verify the synthesized draft is retrievable via GET /api/v1/workflows
    list_resp = client.get("/api/v1/workflows", params={"tenant_id": "tenant_api_test"})
    assert list_resp.status_code == 200
    ids = [w["id"] for w in list_resp.json()]
    assert wf_id in ids

    # Step 2: Approve configuration v1 -> becomes ACTIVE
    app_resp = client.post(f"/api/v1/workflows/{wf_id}/approve?version=1")
    assert app_resp.status_code == 200
    assert app_resp.json()["active_version"] == 1

    # Step 3: Execute Live Run on the synthesized workflow -> produces PO drafts with wa.me link
    run_resp = client.post(
        f"/api/v1/workflows/{wf_id}/runs",
        json={"csv_content": csv_text},
    )
    assert run_resp.status_code == 200
    run_data = run_resp.json()
    assert run_data["status"] == "COMPLETED"
    assert len(run_data["output"]["drafts"]) >= 1
    assert "https://wa.me/628" in run_data["output"]["drafts"][0]["wa_url"]
    assert run_data["output"]["drafts"][0]["status"] == "Draf diserahkan ke owner"

    # Step 4: Verify Kirata Builder HTML UI contains the Synthesizer Hero UI elements
    ui_resp = client.get("/kirata")
    assert ui_resp.status_code == 200
    assert "synthProblemInput" in ui_resp.text
    assert "runSynthesizer()" in ui_resp.text


