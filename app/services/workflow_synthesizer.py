"""
app/services/workflow_synthesizer.py
General Problem-to-Workflow Synthesizer with Evaluator-Optimizer Loop,
Semantic Dry-Run Sanity Checks, and Honest Out-of-Domain Rejection.

Engine invariant: This synthesizer must NEVER branch on specific template_id strings.
It discovers available capabilities dynamically from ToolRegistry and composes
validated DAGs across any registered tool pack.
"""

import inspect
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
        last_planner_error: Optional[str] = None

        for attempt in range(1, cls.MAX_ITERATIONS + 1):
            iterations = attempt
            last_planner_error = None
            if custom_candidate_generator:
                raw_draft = custom_candidate_generator(user_problem, catalog, validation_Feedback)
                draft = await raw_draft if inspect.isawaitable(raw_draft) else raw_draft
            else:
                draft = await cls._llm_candidate_generator(
                    user_problem=user_problem,
                    catalog=catalog,
                    data_summary=data_summary,
                    previous_errors=validation_Feedback,
                )

            if draft.get("_planner_error"):
                last_planner_error = str(draft["_planner_error"])
                validation_Feedback = [last_planner_error]
                if draft.get("_fatal_unavailable"):
                    return SynthesisProposalCard(
                        is_supported=False,
                        rejection_reason="LLM_UNAVAILABLE",
                        iterations_used=iterations,
                        rationale="Provider LLM tidak tersedia untuk merakit workflow.",
                    )
                continue

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
            try:
                candidate_wf = cls._build_workflow_definition(tenant_id, draft)
            except Exception as exc:
                last_planner_error = f"Struktur DAG tidak valid: {exc}"
                validation_Feedback = [last_planner_error]
                continue

            val_result = WorkflowValidator.validate_workflow(candidate_wf, enforce_tool_allowlist=True)

            if val_result.is_valid:
                break

            # Optimizer step: record errors and let next iteration repair the DAG
            validation_Feedback = list(val_result.errors)

        if last_planner_error is not None and (not val_result or not val_result.is_valid):
            return SynthesisProposalCard(
                is_supported=False,
                rejection_reason="LLM_UNAVAILABLE",
                iterations_used=iterations,
                rationale=f"Planner LLM gagal menghasilkan DAG valid setelah {iterations} percobaan: {last_planner_error}",
            )

        if not candidate_wf or not val_result or not val_result.is_valid:
            err_text = "; ".join(val_result.errors) if val_result else "Struktur DAG kosong"
            return SynthesisProposalCard(
                is_supported=False,
                validation=val_result,
                rejection_reason=f"Workflow ditolak validator: {err_text}",
                iterations_used=iterations,
                rationale=err_text,
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
    def _get_llm_provider(cls) -> Optional[Dict[str, Any]]:
        """Mengambil provider LLM aktif tanpa fallback rule-based."""
        from app.services.deepseek_agent import DeepSeekAgentService
        providers = DeepSeekAgentService._get_active_providers()
        return providers[0] if providers else None

    @classmethod
    def _build_planner_prompt(
        cls,
        user_problem: str,
        catalog: List[Dict[str, Any]],
        data_summary: Optional[Dict[str, Any]],
        previous_errors: List[str],
    ) -> List[Dict[str, str]]:
        catalog_json = json.dumps(catalog, ensure_ascii=False)
        system_msg = (
            "Anda adalah Planner Workflow DAG Kirata. Tugas Anda merakit DAG tervalidasi dari katalog tool resmi.\n"
            "ATURAN WAJIB:\n"
            "1. DILARANG mengarang tool_type di luar katalog resmi yang diberikan.\n"
            "2. Jika masalah user di luar kemampuan katalog (misal pajak, payroll, trading, CCTV), kembalikan "
            '{"is_supported": false, "feature_gap": "<nama domain>", "rejection_reason": "Maaf, Kirata belum memiliki tool untuk domain ini."}.\n'
            "3. Setiap node dengan requires_upstream_gate=true WAJIB didahului node governance.action_approval.\n"
            "4. Kembalikan HANYA JSON valid dengan format:\n"
            '{"is_supported": true, "name": "...", "rationale": "...", '
            '"nodes": [{"id": "...", "tool_type": "...", "config": {}, "input_bindings": {}}], '
            '"edges": [{"source": "...", "target": "..."}]}\n\n'
            f"KATALOG TOOL RESMI:\n{catalog_json}"
        )
        user_parts = [f"Masalah user: {user_problem}"]
        if data_summary:
            user_parts.append(f"Ringkasan data sampel: {json.dumps(data_summary, ensure_ascii=False)}")
        if previous_errors:
            user_parts.append(
                "Error validasi pada percobaan sebelumnya (WAJIB diperbaiki):\n- " + "\n- ".join(previous_errors)
            )
        return [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": "\n\n".join(user_parts)},
        ]

    @classmethod
    async def _llm_candidate_generator(
        cls,
        user_problem: str,
        catalog: List[Dict[str, Any]],
        data_summary: Optional[Dict[str, Any]],
        previous_errors: List[str],
    ) -> Dict[str, Any]:
        """Memanggil LLM untuk merakit kandidat DAG dari katalog ToolRegistry."""
        provider = cls._get_llm_provider()
        if not provider:
            return {"_planner_error": "Provider LLM tidak tersedia.", "_fatal_unavailable": True}

        messages = cls._build_planner_prompt(user_problem, catalog, data_summary, previous_errors)
        try:
            response = await provider["client"].chat.completions.create(
                model=provider["model"],
                messages=messages,
                temperature=0.1,
            )
            raw_text = (response.choices[0].message.content or "").strip()
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text, flags=re.I | re.S).strip()
            parsed = json.loads(cleaned)
            if not isinstance(parsed, dict):
                return {"_planner_error": "Output LLM bukan objek JSON."}
            return parsed
        except Exception as exc:
            logger.error(f"Planner LLM gagal memproses DAG: {exc}")
            return {"_planner_error": f"LLM_PLANNER_ERROR: {exc}"}

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

