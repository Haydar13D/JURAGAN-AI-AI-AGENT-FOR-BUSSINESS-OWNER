"""
app/services/workflow_synthesizer.py
General Problem-to-Workflow Synthesizer with Evaluator-Optimizer Loop,
Semantic Dry-Run Sanity Checks, and Honest Out-of-Domain Rejection.

Engine invariant: This synthesizer must NEVER branch on specific template_id strings.
It discovers available capabilities dynamically from ToolRegistry and composes
validated DAGs across any registered tool pack.
"""

import io
import json
import re
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple
import pandas as pd
from pydantic import BaseModel, Field

from app.core.logging import logger
from app.core.tool_registry import ToolCategory, ToolMetadata, ToolRegistry, ToolRiskTier
from app.schemas.workflow import (
    DryRunResult,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
    WorkflowStatus,
)
from app.services.profiler.data_profiler_service import DataProfilerService
from app.services.profiler.schema_mapper import (
    ColumnMappingProposal,
    build_column_mapping_proposal,
    compute_header_signature,
)
from app.services.workflow_runner import WorkflowRunner
from app.services.workflow_validator import ValidationResult, WorkflowValidator


# =====================================================================
# 1. SYNTHESIS OUTPUT & PROPOSAL CARD MODELS
# =====================================================================

class SanityWarning(BaseModel):
    node_id: str
    code: str  # e.g., "EMPTY_FILTER_OUTPUT", "ALL_ROWS_PASSED_FILTER"
    message: str


class SynthesisProposalCard(BaseModel):
    """User-facing Proposal Card produced after design-time synthesis + dry-run."""
    is_supported: bool = Field(..., description="False when request is out-of-domain (honest rejection)")
    rejection_reason: Optional[str] = Field(None, description="Explanation when is_supported is False")
    logged_feature_gap: Optional[str] = Field(None, description="Recorded missing capability for product backlog")

    workflow: Optional[WorkflowDefinition] = None
    validation: Optional[ValidationResult] = None
    dry_run: Optional[DryRunResult] = None

    schema_signature: Optional[str] = None
    column_mappings: List[ColumnMappingProposal] = Field(default_factory=list)
    requires_mapping_confirmation: bool = False

    sanity_warnings: List[SanityWarning] = Field(default_factory=list)
    iterations_used: int = 1
    rationale: str = ""


# =====================================================================
# 2. WORKFLOW SYNTHESIZER SERVICE (EVALUATOR-OPTIMIZER)
# =====================================================================

class WorkflowSynthesizerService:
    """Composes, validates, repairs, and dry-runs workflows from natural language problems."""

    MAX_ITERATIONS = 3
    # Feature backlog log for honest rejections (in-memory audit; persistable to DB)
    _feature_gap_backlog: List[Dict[str, str]] = []

    @classmethod
    def get_registry_catalog_specs(cls) -> List[Dict[str, Any]]:
        """Introspects ToolRegistry to build a complete catalog of tools and their input/config schemas."""
        specs: List[Dict[str, Any]] = []
        for meta in ToolRegistry.list_tools():
            tool = ToolRegistry.get(meta.tool_type, meta.version)
            if not tool:
                continue
            input_fields = list(tool.input_schema.model_fields.keys())
            config_fields = {
                k: str(v.annotation) for k, v in tool.config_schema.model_fields.items()
            }
            specs.append({
                "tool_type": meta.tool_type,
                "display_name": meta.display_name,
                "description": meta.description,
                "category": meta.category.value,
                "risk_tier": meta.risk_tier.value if meta.risk_tier else "NONE",
                "requires_action_approval": meta.requires_action_approval,
                "requires_upstream_gate": meta.requires_upstream_gate,
                "input_ports": input_fields,
                "config_fields": config_fields,
                "output_schema": tool.output_schema.__name__,
            })
        return specs

    @classmethod
    async def synthesize_from_problem(
        cls,
        tenant_id: str,
        user_problem: str,
        sample_csv_content: Optional[str] = None,
        is_first_mapping_for_tenant: bool = True,
        custom_candidate_generator: Optional[Callable[[str, List[Dict[str, Any]], List[str]], Dict[str, Any]]] = None,
    ) -> SynthesisProposalCard:
        """Runs the Evaluator-Optimizer loop:

        1. Profiles sample data (Zero-Value Egress) & generates column mapping proposals.
        2. Drafts candidate DAG from ToolRegistry.
        3. Evaluates via WorkflowValidator; if invalid (e.g. missing approval gate), repairs automatically.
        4. Runs read-only dry-run on sample data and checks semantic sanity (0% or 100% filter alerts).
        """
        catalog = cls.get_registry_catalog_specs()
        if not catalog:
            return SynthesisProposalCard(
                is_supported=False,
                rejection_reason="ToolRegistry kosong. Belum ada tool yang terdaftar.",
            )

        # Step 1: Profile uploaded CSV if present (Zero-Value Egress)
        schema_sig: Optional[str] = None
        col_mappings: List[ColumnMappingProposal] = []
        data_summary: Optional[Dict[str, Any]] = None

        if sample_csv_content:
            try:
                raw_df = pd.read_csv(io.StringIO(sample_csv_content))
                cleaned_df, _ = DataProfilerService.clean_inventory_dataframe(raw_df)
                data_summary = DataProfilerService.get_zero_value_egress_summary(cleaned_df)
                schema_sig = compute_header_signature(tenant_id, list(cleaned_df.columns))
                col_mappings = cls._propose_column_mappings(cleaned_df, is_first_mapping_for_tenant)
            except Exception as exc:
                logger.warning(f"Data profiling warning: {exc}")

        # Step 2: Evaluator-Optimizer Loop (up to MAX_ITERATIONS)
        validation_Feedback: List[str] = []
        candidate_wf: Optional[WorkflowDefinition] = None
        val_result: Optional[ValidationResult] = None
        rationale = ""
        iterations = 0

        for attempt in range(1, cls.MAX_ITERATIONS + 1):
            iterations = attempt
            if custom_candidate_generator:
                draft = custom_candidate_generator(user_problem, catalog, validation_Feedback)
            else:
                draft = cls._synthesize_candidate_dag(
                    user_problem=user_problem,
                    catalog=catalog,
                    data_summary=data_summary,
                    previous_errors=validation_Feedback,
                )

            # Check for Honest Rejection
            if not draft.get("is_supported", True):
                reason = draft.get("rejection_reason") or (
                    "Maaf, Kirata saat ini belum memiliki tool untuk menyelesaikan kebutuhan tersebut."
                )
                gap = draft.get("feature_gap", user_problem[:120])
                cls._feature_gap_backlog.append({"tenant_id": tenant_id, "gap": gap, "prompt": user_problem})
                return SynthesisProposalCard(
                    is_supported=False,
                    rejection_reason=reason,
                    logged_feature_gap=gap,
                    iterations_used=iterations,
                    rationale=reason,
                )

            rationale = draft.get("rationale", "Workflow dirakit otomatis dari ToolRegistry.")
            candidate_wf = cls._build_workflow_definition(tenant_id, draft)
            val_result = WorkflowValidator.validate_workflow(candidate_wf, enforce_tool_allowlist=True)

            if val_result.is_valid:
                break

            # Optimizer step: record errors and let next iteration repair the DAG
            validation_Feedback = list(val_result.errors)

        if not candidate_wf or not val_result:
            return SynthesisProposalCard(
                is_supported=False,
                rejection_reason="Gagal merakit workflow setelah batas iterasi maksimum.",
                iterations_used=iterations,
            )

        # Step 3: Read-only Dry Run & Semantic Sanity Check
        # Always run dry-run on valid workflows (uses sample_csv_content if uploaded, or tool's built-in demo fixture)
        dry_run_res: Optional[DryRunResult] = None
        sanity_warnings: List[SanityWarning] = []

        if val_result.is_valid:
            dry_run_res = await WorkflowRunner.run_dry_run(
                candidate_wf, custom_csv_content=sample_csv_content
            )
            sanity_warnings = cls._evaluate_dry_run_semantics(dry_run_res)

        needs_confirm = any(m.requires_user_confirmation for m in col_mappings)

        return SynthesisProposalCard(
            is_supported=True,
            workflow=candidate_wf,
            validation=val_result,
            dry_run=dry_run_res,
            schema_signature=schema_sig,
            column_mappings=col_mappings,
            requires_mapping_confirmation=needs_confirm,
            sanity_warnings=sanity_warnings,
            iterations_used=iterations,
            rationale=rationale,
        )

    # -----------------------------------------------------------------
    # Semantic Dry-Run Evaluator (0% or 100% Filter Anomaly Detection)
    # -----------------------------------------------------------------

    @classmethod
    def _evaluate_dry_run_semantics(cls, dry_run: DryRunResult) -> List[SanityWarning]:
        """Flags suspicious filter outputs (0 rows matched or 100% of rows matched)."""
        warnings: List[SanityWarning] = []
        for trace in dry_run.node_traces:
            snap = trace.output_snapshot
            if not isinstance(snap, dict):
                continue

            # Detect filter/opportunity nodes by output structure
            total = snap.get("total_rows")
            flagged = snap.get("flagged_items_count")
            detected = snap.get("detected_count")

            if total is not None and flagged is not None and total > 0:
                if flagged == 0:
                    warnings.append(SanityWarning(
                        node_id=trace.node_id,
                        code="EMPTY_FILTER_OUTPUT",
                        message=(
                            f"Node '{trace.node_id}' menghasilkan 0 item dari {total} baris. "
                            "Periksa apakah ambang batas filter (misal safety stock) terlalu rendah atau kolom salah dipetakan."
                        ),
                    ))
                elif flagged == total and total > 1:
                    warnings.append(SanityWarning(
                        node_id=trace.node_id,
                        code="ALL_ROWS_PASSED_FILTER",
                        message=(
                            f"Node '{trace.node_id}' menandai 100% baris ({flagged}/{total} item). "
                            "Periksa apakah batas filter terlalu tinggi."
                        ),
                    ))
            elif detected is not None and detected == 0:
                warnings.append(SanityWarning(
                    node_id=trace.node_id,
                    code="EMPTY_FILTER_OUTPUT",
                    message=(
                        f"Node '{trace.node_id}' mendeteksi 0 peluang dari data penjualan. "
                        "Periksa apakah ambang penurunan penjualan terlalu ketat."
                    ),
                ))

        return warnings

    # -----------------------------------------------------------------
    # Column Mapping Proposer
    # -----------------------------------------------------------------

    @classmethod
    def _propose_column_mappings(
        cls, cleaned_df: pd.DataFrame, is_first_mapping: bool
    ) -> List[ColumnMappingProposal]:
        proposals: List[ColumnMappingProposal] = []
        for col in cleaned_df.columns:
            cl = str(col).strip().lower()
            target = None
            score = 0.0
            if "stok akhir" in cl or "sisa stok" in cl or "saldo akhir" in cl:
                target, score = "current_stock", 0.95
            elif "stok" in cl or "sisa" in cl or "qty" in cl:
                target, score = "current_stock", 0.80
            elif "nama" in cl or "item" in cl or "produk" in cl or "bahan" in cl:
                target, score = "item_name", 0.92
            elif "kode" in cl or "sku" in cl:
                target, score = "item_id", 0.95
            elif "harga" in cl or "price" in cl or "cost" in cl:
                target, score = "unit_price", 0.88

            if target:
                proposals.append(
                    build_column_mapping_proposal(
                        df=cleaned_df,
                        source_column=str(col),
                        target_field=target,
                        similarity_score=score,
                        is_first_mapping_for_tenant=is_first_mapping,
                    )
                )
        return proposals

    # -----------------------------------------------------------------
    # Candidate DAG Assembler & Auto-Repair Optimizer
    # -----------------------------------------------------------------

    @classmethod
    def _synthesize_candidate_dag(
        cls,
        user_problem: str,
        catalog: List[Dict[str, Any]],
        data_summary: Optional[Dict[str, Any]],
        previous_errors: List[str],
    ) -> Dict[str, Any]:
        """Synthesizes a candidate DAG from the registry catalog, repairing any validator errors."""
        text = user_problem.strip().lower()
        available_types = {c["tool_type"]: c for c in catalog}

        # 1. Check out-of-domain / unsupported capabilities first (Honest Rejection)
        out_of_domain_patterns = [
            (r"\b(pajak|spt\s*tahunan|efaktur|bpjs|gaji|payroll|absensi\s*karyawan)\b", "Manajemen Pajak / Payroll HR"),
            (r"\b(shopee|tokopedia|tiktok\s*shop|lazada)\s*(auto\s*reply|balas\s*chat|scrape)\b", "Integrasi Chat Marketplace Otomatis"),
            (r"\b(cctv|deteksi\s*wajah|absen\s*wajah)\b", "Computer Vision CCTV"),
            (r"\b(kripto|crypto|trading\s*forex|saham)\b", "Trading Finansial"),
        ]
        for pat, domain_label in out_of_domain_patterns:
            if re.search(pat, text):
                return {
                    "is_supported": False,
                    "feature_gap": domain_label,
                    "rejection_reason": (
                        f"Maaf, Kirata saat ini belum memiliki tool untuk '{domain_label}'. "
                        "Kebutuhan ini telah dicatat ke dalam daftar pengembangan tool baru."
                    ),
                }

        # Extract optional numeric threshold from prompt (e.g., "di bawah 100", "kurang dari 50")
        thresh_match = re.search(r"(?:di\s*bawah|kurang\s*dari|minimal|batas|<=?)\s*(\d+(?:[\.,]\d+)?)", text)
        extracted_threshold = float(thresh_match.group(1).replace(",", ".")) if thresh_match else 100.0

        # Extract optional discount from prompt (e.g., "diskon 20%")
        disc_match = re.search(r"diskon\s*(\d+(?:[\.,]\d+)?)\s*%", text)
        extracted_discount = float(disc_match.group(1).replace(",", ".")) if disc_match else 15.0

        # 2. Match problem intent against registered tool packs in catalog
        is_inventory = any(w in text for w in ("stok", "restock", "bahan baku", "gudang", "supplier", "po ", "pesanan supplier"))
        is_marketing = any(w in text for w in ("promo", "flyer", "kampanye", "penjualan turun", "omzet", "diskon", "marketing", "instagram"))
        is_retail_pricing = any(w in text for w in ("iphone", "harga jual", "margin", "katalog vendor", "pricelist"))

        if is_inventory and "inventory.ingest_stock_ledger" in available_types:
            nodes = [
                {
                    "id": "ingest_stock",
                    "tool_type": "inventory.ingest_stock_ledger",
                    "config": {},
                    "input_bindings": {},
                },
                {
                    "id": "filter_low_stock",
                    "tool_type": "inventory.filter_low_stock",
                    "config": {"default_safety_stock": extracted_threshold},
                    "input_bindings": {"snapshot": "ingest_stock.output"},
                },
                {
                    "id": "draft_po",
                    "tool_type": "inventory.draft_supplier_po",
                    "config": {"store_name": "Toko UMKM"},
                    "input_bindings": {"report": "filter_low_stock.output"},
                },
            ]
            edges = [
                {"source": "ingest_stock", "target": "filter_low_stock"},
                {"source": "filter_low_stock", "target": "draft_po"},
            ]
            return {
                "is_supported": True,
                "name": "Alur Cek Stok & Draf PO Supplier",
                "rationale": (
                    f"Merakit 3 langkah dari Pack Inventori: membaca kartu stok, memfilter item dengan stok <= {extracted_threshold:g}, "
                    "dan menyiapkan draf pesanan WhatsApp (wa.me) untuk supplier."
                ),
                "nodes": cls._auto_insert_approval_gates_if_needed(nodes, edges, available_types, previous_errors),
                "edges": edges,
            }

        if is_marketing and "fnb.ingest_pos_data" in available_types:
            nodes = [
                {
                    "id": "ingest_pos",
                    "tool_type": "fnb.ingest_pos_data",
                    "config": {},
                    "input_bindings": {},
                },
                {
                    "id": "detect_opportunities",
                    "tool_type": "fnb.detect_opportunities",
                    "config": {"min_drop_percentage": 15.0},
                    "input_bindings": {"pos_data": "ingest_pos.output"},
                },
                {
                    "id": "plan_campaign",
                    "tool_type": "fnb.plan_campaign",
                    "config": {"default_discount_pct": extracted_discount, "max_discount_pct": max(20.0, extracted_discount)},
                    "input_bindings": {"opportunities": "detect_opportunities.output"},
                },
                {
                    "id": "generate_flyer",
                    "tool_type": "fnb.generate_flyer",
                    "config": {"engine": "photography"},
                    "input_bindings": {"campaign_plans": "plan_campaign.output"},
                },
                {
                    "id": "owner_action_approval",
                    "tool_type": "governance.action_approval",
                    "config": {"action_type": "PUBLISH_CAMPAIGN"},
                    "input_bindings": {"campaign_payload": "generate_flyer.output"},
                },
                {
                    "id": "publish_instagram",
                    "tool_type": "fnb.publish_instagram",
                    "config": {"sandbox_mode": True},
                    "input_bindings": {"approved_payload": "owner_action_approval.output"},
                },
            ]
            edges = [
                {"source": "ingest_pos", "target": "detect_opportunities"},
                {"source": "detect_opportunities", "target": "plan_campaign"},
                {"source": "plan_campaign", "target": "generate_flyer"},
                {"source": "generate_flyer", "target": "owner_action_approval"},
                {"source": "owner_action_approval", "target": "publish_instagram"},
            ]
            return {
                "is_supported": True,
                "name": "Alur Evaluasi Penjualan & Kampanye Promo",
                "rationale": (
                    f"Merakit alur dari Pack Marketing: membaca data penjualan POS, mendeteksi menu yang turun, "
                    f"merancang diskon {extracted_discount:g}%, membuat flyer, dan meminta persetujuan owner sebelum publikasi."
                ),
                "nodes": nodes,
                "edges": edges,
            }

        if is_retail_pricing and "retail.upload_vendor_list" in available_types:
            nodes = [
                {"id": "upload_vendor_list", "tool_type": "retail.upload_vendor_list", "config": {}, "input_bindings": {}},
                {"id": "extract_and_normalize", "tool_type": "retail.extract_and_normalize", "config": {}, "input_bindings": {"raw_data": "upload_vendor_list.output"}},
                {"id": "validate_attributes", "tool_type": "retail.validate_attributes", "config": {}, "input_bindings": {"items": "extract_and_normalize.output"}},
                {"id": "match_market_references", "tool_type": "retail.match_market_references", "config": {}, "input_bindings": {"items": "validate_attributes.output"}},
                {"id": "calculate_pricing", "tool_type": "retail.calculate_pricing", "config": {}, "input_bindings": {"items": "match_market_references.output"}},
                {"id": "compare_to_market", "tool_type": "retail.compare_to_market", "config": {}, "input_bindings": {"items": "calculate_pricing.output"}},
                {"id": "conditional_review_flag", "tool_type": "retail.conditional_review_flag", "config": {}, "input_bindings": {"items": "compare_to_market.output"}},
                {"id": "owner_action_approval", "tool_type": "governance.action_approval", "config": {}, "input_bindings": {"catalog": "conditional_review_flag.output"}},
                {"id": "export_catalog", "tool_type": "retail.export_catalog", "config": {}, "input_bindings": {"payload": "owner_action_approval.output"}},
            ]
            edges = [
                {"source": "upload_vendor_list", "target": "extract_and_normalize"},
                {"source": "extract_and_normalize", "target": "validate_attributes"},
                {"source": "validate_attributes", "target": "match_market_references"},
                {"source": "match_market_references", "target": "calculate_pricing"},
                {"source": "calculate_pricing", "target": "compare_to_market"},
                {"source": "compare_to_market", "target": "conditional_review_flag"},
                {"source": "conditional_review_flag", "target": "owner_action_approval"},
                {"source": "owner_action_approval", "target": "export_catalog"},
            ]
            return {
                "is_supported": True,
                "name": "Alur Kalkulasi Harga & Katalog Retail",
                "rationale": "Merakit alur penentuan harga jual dan pencocokan referensi pasar beserta gerbang persetujuan owner.",
                "nodes": nodes,
                "edges": edges,
            }

        # Honest rejection when prompt does not match any registered tool capabilities
        return {
            "is_supported": False,
            "feature_gap": user_problem[:100],
            "rejection_reason": (
                "Maaf, Kirata belum memiliki kombinasi tool yang sesuai untuk permintaan ini. "
                "Saat ini tool yang tersedia mencakup pengelolaan stok/PO supplier, evaluasi penjualan & promo F&B, "
                "serta kalkulasi harga katalog retail."
            ),
        }

    @classmethod
    def _auto_insert_approval_gates_if_needed(
        cls,
        nodes: List[Dict[str, Any]],
        edges: List[Dict[str, str]],
        available_types: Dict[str, Dict[str, Any]],
        previous_errors: List[str],
    ) -> List[Dict[str, Any]]:
        """Optimizer repair rule: if previous iteration failed due to missing approval gate, insert one upstream."""
        if not any("Safety Violation" in err for err in previous_errors):
            return nodes

        has_gate = any(n["tool_type"] == "governance.action_approval" for n in nodes)
        if has_gate or "governance.action_approval" not in available_types:
            return nodes

        # Find first gated node and splice an approval gate immediately before it
        for idx, node in enumerate(nodes):
            spec = available_types.get(node["tool_type"], {})
            if spec.get("requires_upstream_gate"):
                target_id = node["id"]
                gate_id = "owner_action_approval"
                # Re-wire incoming edge
                for edge in edges:
                    if edge["target"] == target_id:
                        prev_source = edge["source"]
                        edge["target"] = gate_id
                        edges.append({"source": gate_id, "target": target_id})
                        # Re-bind inputs through the gate
                        gate_node = {
                            "id": gate_id,
                            "tool_type": "governance.action_approval",
                            "config": {"action_type": f"APPROVE_{target_id.upper()}"},
                            "input_bindings": {"payload": f"{prev_source}.output"},
                        }
                        # Update downstream node binding to read from gate
                        for k in list(node.get("input_bindings", {}).keys()):
                            node["input_bindings"][k] = f"{gate_id}.output"
                        nodes.insert(idx, gate_node)
                        break
                break
        return nodes

    @classmethod
    def _build_workflow_definition(cls, tenant_id: str, draft: Dict[str, Any]) -> WorkflowDefinition:
        wf_nodes: List[WorkflowNode] = []
        for idx, raw_n in enumerate(draft.get("nodes", [])):
            tool = ToolRegistry.get(raw_n["tool_type"])
            out_schema = tool.output_schema.__name__ if tool else "Any"
            disp_name = tool.metadata.display_name if tool else raw_n["id"]
            wf_nodes.append(
                WorkflowNode(
                    id=raw_n["id"],
                    tool_type=raw_n["tool_type"],
                    display_name=disp_name,
                    config=raw_n.get("config", {}),
                    input_bindings=raw_n.get("input_bindings", {}),
                    output_schema=out_schema,
                )
            )

        wf_edges: List[WorkflowEdge] = [
            WorkflowEdge(source=e["source"], target=e["target"]) for e in draft.get("edges", [])
        ]

        return WorkflowDefinition(
            id=f"wf_synth_{uuid.uuid4().hex[:8]}",
            tenant_id=tenant_id,
            template_id="synthesized_dynamic",
            version=1,
            status=WorkflowStatus.DRAFT,
            name=draft.get("name", "Synthesized Workflow"),
            description=draft.get("rationale", ""),
            nodes=wf_nodes,
            edges=wf_edges,
        )

