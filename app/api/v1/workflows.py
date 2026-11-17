"""
app/api/v1/workflows.py
REST API endpoints for Kirata AI Workflow Builder.
Template gallery, tool catalog, workflow instances, proposals, validation, dry-run,
configuration approval, live runs, and business action approvals.
"""

import json
import uuid
from datetime import datetime
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel, Field

from app.schemas.workflow import (
    WorkflowDefinition,
    WorkflowProposal,
    DryRunResult,
    RunStatus,
    ActionApprovalRequest,
    ActionDecisionRequest,
    WorkflowStatus,
    PatchOperation,
    PatchOpType,
    BusinessProposal,
    ConfigPatchOperation,
)
from app.core.tool_registry import ToolRegistry
from app.core.logging import logger
from app.core.database import AsyncSessionLocal
from app.services.workflow_repository import WorkflowRepository
from app.services.workflow_validator import WorkflowValidator, ValidationResult
from app.services.patch_engine import PatchEngine
from app.services.proposal_generator import ProposalGenerator
from app.services.business_config_service import BusinessConfigService
from app.services.workflow_runner import WorkflowRunner
from app.services.retail_pricing import register_retail_tools
from app.services.fnb_marketing import register_fnb_tools
from app.services.inventory_pack import register_inventory_tools
from app.services.workflow_synthesizer import SynthesisProposalCard, WorkflowSynthesizerService
from app.services import template_catalog

router = APIRouter(prefix="/workflows", tags=["Kirata Workflow Builder"])

register_retail_tools()
register_fnb_tools()
register_inventory_tools()


# =====================================================================
# REQUEST SCHEMAS
# =====================================================================

class CreateFromTemplateRequest(BaseModel):
    template_id: str = "tpl_used_iphone_pricing_v1"
    tenant_id: str = "tenant_juragan_retail"
    name: Optional[str] = None
    onboarding: Dict[str, Any] = Field(default_factory=dict, description="Nilai form onboarding template")


class ProposalRequest(BaseModel):
    instruction: str = Field(..., description="Instruksi perubahan alur kerja dalam Bahasa Indonesia")


class LiveRunRequest(BaseModel):
    csv_content: Optional[str] = None


class SynthesizeWorkflowRequest(BaseModel):
    tenant_id: str = Field(default="tenant_umkm_default", description="Tenant identifier")
    user_problem: str = Field(..., description="Deskripsi masalah bisnis user dalam Bahasa Indonesia")
    sample_csv_content: Optional[str] = Field(None, description="Isi file CSV sampel untuk profiling dan dry-run")
    is_first_mapping_for_tenant: bool = Field(default=True, description="True jika skema baru pertama kali dipetakan")
    save_as_draft: bool = Field(default=True, description="Simpan hasil sintesis ke daftar workflow sebagai DRAFT")


# =====================================================================
# HELPERS
# =====================================================================

async def _get_versions_db(session: Any, workflow_id: str) -> Dict[int, WorkflowDefinition]:
    versions = await WorkflowRepository.get_all_versions_dict(session, workflow_id)
    if not versions:
        raise HTTPException(status_code=404, detail=f"Alur kerja '{workflow_id}' tidak ditemukan.")
    return versions


async def _workflow_summary_db(session: Any, workflow_id: str) -> Dict[str, Any]:
    summary = await WorkflowRepository.get_workflow_summary(session, workflow_id)
    if not summary:
        raise HTTPException(status_code=404, detail=f"Alur kerja '{workflow_id}' tidak ditemukan.")
    return summary


# =====================================================================
# TEMPLATE GALLERY & TOOL CATALOG (static routes first)
# =====================================================================

@router.post("/synthesize", summary="Sintesis workflow baru dari deskripsi masalah + dry-run otomatis")
async def synthesize_workflow(req: SynthesizeWorkflowRequest) -> SynthesisProposalCard:
    card = await WorkflowSynthesizerService.synthesize_from_problem(
        tenant_id=req.tenant_id,
        user_problem=req.user_problem,
        sample_csv_content=req.sample_csv_content,
        is_first_mapping_for_tenant=req.is_first_mapping_for_tenant,
    )
    if card.is_supported and card.workflow and req.save_as_draft:
        try:
            async with AsyncSessionLocal() as session:
                await WorkflowRepository.save_workflow(session, card.workflow, is_active=False)
        except Exception as exc:
            logger.error(f"Gagal menyimpan workflow hasil sintesis '{card.workflow.id}' ke DB: {exc}")
            raise HTTPException(status_code=500, detail=f"Gagal menyimpan workflow ke basis data: {exc}")
    return card


@router.post("/parse-file", summary="Parse file POS (Excel .xlsx / CSV) menjadi data transaksi standar")
async def parse_pos_file_endpoint(file: UploadFile = File(...)) -> Dict[str, Any]:
    """
    Parses uploaded POS data file (.xlsx, .xls, .csv).
    Detects custom multi-table layouts (e.g. Daily Report with Menu & Jumlah),
    standard tables, and returns clean CSV text along with detected product names.
    """
    import io
    import pandas as pd

    filename = file.filename or "upload.csv"
    content_bytes = await file.read()

    if filename.lower().endswith((".xlsx", ".xls")):
        try:
            df = pd.read_excel(io.BytesIO(content_bytes))
            return {
                "success": True,
                "filename": filename,
                "csv_content": df.to_csv(index=False),
                "total_rows": len(df),
                "detected_products": [str(c) for c in df.iloc[:, 0].head(5)] if len(df.columns) > 0 else [],
                "message": f"Berhasil membaca file Excel {filename} ({len(df)} baris)."
            }
        except Exception as exc:
            logger.error(f"Error parsing Excel file {filename}: {exc}")
            raise HTTPException(status_code=400, detail=f"Gagal membaca file Excel: {str(exc)}")
    else:
        try:
            text = content_bytes.decode("utf-8", errors="replace")
            return {
                "success": True,
                "filename": filename,
                "csv_content": text,
                "total_rows": len(text.strip().split("\n")) if text.strip() else 0,
                "detected_products": [],
                "message": f"File CSV '{filename}' dimuat."
            }
        except Exception as exc:
            logger.error(f"Error parsing CSV file {filename}: {exc}")
            raise HTTPException(status_code=400, detail=f"Gagal membaca file CSV: {str(exc)}")


@router.get("/templates", summary="Template gallery: outcome, input, tools, batasan, output")
def list_templates() -> List[Dict[str, Any]]:
    return template_catalog.list_catalog()


@router.get("/templates/{template_id}", summary="Detail template + definisi kanonikal")
def get_template(template_id: str) -> Dict[str, Any]:
    entry = template_catalog.get_catalog_entry(template_id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"Template '{template_id}' tidak ditemukan.")
    entry["definition"] = template_catalog.load_template_definition(template_id)
    return entry


@router.get("/tools", summary="Katalog tool tersertifikasi (allowlist) beserta schema konfigurasi")
def list_tools() -> List[Dict[str, Any]]:
    result = []
    for meta in ToolRegistry.list_tools():
        tool = ToolRegistry.get(meta.tool_type, meta.version)
        try:
            config_schema = tool.config_schema.model_json_schema() if tool else {}
        except Exception:
            config_schema = {}
        item = meta.model_dump()
        item["config_schema"] = config_schema
        result.append(item)
    return result


@router.get("", summary="Daftar workflow milik tenant")
async def list_workflows(tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
    try:
        async with AsyncSessionLocal() as session:
            return await WorkflowRepository.list_workflows(session, tenant_id=tenant_id)
    except Exception as exc:
        logger.error(f"Gagal memuat daftar workflow dari database: {exc}")
        raise HTTPException(status_code=500, detail=f"Gagal memuat daftar workflow: {exc}")


@router.get("/runs", summary="Riwayat run semua workflow")
async def list_runs(
    tenant_id: Optional[str] = None,
    workflow_id: Optional[str] = None,
    status: Optional[str] = None,
    run_mode: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    try:
        async with AsyncSessionLocal() as session:
            return await WorkflowRepository.list_runs(
                session, tenant_id=tenant_id, workflow_id=workflow_id,
                status=status, run_mode=run_mode, limit=limit
            )
    except Exception as exc:
        logger.error(f"Error querying runs from database: {exc}")
        raise HTTPException(status_code=500, detail=f"Gagal memuat riwayat run: {exc}")


@router.get("/runs/{run_id}/events", summary="Timeline event eksekusi sebuah run beserta memory snapshot")
async def get_run_events(run_id: str) -> Dict[str, Any]:
    try:
        async with AsyncSessionLocal() as session:
            detail = await WorkflowRepository.get_run_detail(session, run_id)
            if not detail:
                raise HTTPException(status_code=404, detail=f"Run '{run_id}' tidak ditemukan.")
            return detail
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Error querying run detail from database: {exc}")
        raise HTTPException(status_code=500, detail=f"Gagal memuat detail run: {exc}")


@router.post("/runs/{run_id}/actions/{action_id}/decision",
             summary="Tier 2: Business Action Approval — setujui/tolak ekspor katalog")
async def decide_business_action(run_id: str, action_id: str, req: ActionDecisionRequest) -> Dict[str, Any]:
    async with AsyncSessionLocal() as session:
        run = await WorkflowRepository.get_run_detail(session, run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' tidak ditemukan.")

        pending = await WorkflowRepository.get_pending_action(session, action_id)
        if not pending or pending.run_id != run_id or pending.status != "PENDING":
            raise HTTPException(status_code=404, detail=f"Pending action '{action_id}' tidak ditemukan untuk run ini.")

        if req.decision == "REJECT":
            try:
                await WorkflowRepository.record_action_decision(
                    session=session,
                    run_id=run_id,
                    action_id=action_id,
                    decision="REJECT",
                    reviewer_id=req.reviewer_user_id,
                    decision_reason=req.decision_reason
                )
            except Exception as exc:
                logger.error(f"Failed to record rejection in database: {exc}")
                raise HTTPException(status_code=500, detail=f"Gagal menyimpan keputusan penolakan: {exc}")

            return {
                "status": "REJECTED",
                "message": f"Aksi bisnis ditolak oleh {req.reviewer_user_id}. Tidak ada data yang dipublikasikan/diekspor."
            }

        wf = await WorkflowRepository.get_workflow_version(session, run["workflow_id"], run["version"])
        if not wf:
            raise HTTPException(status_code=404, detail=f"Versi workflow '{run['workflow_id']}' v{run['version']} tidak ditemukan.")

        run_status, node_traces, _, final_out = await WorkflowRunner.run_live(
            workflow=wf,
            run_id=run_id,
            action_approval_granted=True,
            reviewer_id=req.reviewer_user_id,
            db_session=session,
        )
        out_payload = final_out.model_dump() if hasattr(final_out, "model_dump") else None

        try:
            await WorkflowRepository.record_action_decision(
                session=session,
                run_id=run_id,
                action_id=action_id,
                decision="APPROVE",
                reviewer_id=req.reviewer_user_id,
                decision_reason=req.decision_reason,
                final_output=out_payload,
                updated_traces=node_traces
            )
        except Exception as exc:
            logger.error(f"Failed to record approval decision in database: {exc}")
            raise HTTPException(status_code=500, detail=f"Gagal menyimpan keputusan persetujuan: {exc}")

        return {
            "status": "APPROVED_AND_COMPLETED",
            "message": f"Aksi bisnis disetujui oleh {req.reviewer_user_id}.",
            "result": out_payload
        }


# =====================================================================
# WORKFLOW INSTANCES
# =====================================================================

@router.post("/from-template", summary="Membuat workflow DRAFT v1 dari template + form onboarding")
async def create_from_template(req: CreateFromTemplateRequest) -> Dict[str, Any]:
    entry = template_catalog.get_catalog_entry(req.template_id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"Template '{req.template_id}' tidak ditemukan.")
    if entry["status"] != "AVAILABLE":
        raise HTTPException(
            status_code=400,
            detail=f"Template '{entry['name']}' berstatus {entry['status']} dan belum bisa dipakai."
        )

    definition = template_catalog.load_template_definition(req.template_id)
    wf_id = f"wf_{uuid.uuid4().hex[:8]}"
    definition["id"] = wf_id
    definition["tenant_id"] = req.tenant_id
    definition["name"] = req.name or entry["name"]
    definition["version"] = 1
    definition["status"] = WorkflowStatus.DRAFT  # FR-06: draft cannot execute until approved
    definition["created_at"] = datetime.now().isoformat()

    # Apply onboarding form values onto node configs
    node_map = {n["id"]: n for n in definition["nodes"]}
    for field in entry.get("onboarding_fields", []):
        if field["key"] not in req.onboarding:
            continue
        value = req.onboarding[field["key"]]
        if field.get("transform") == "pct_to_ratio":
            value = round(float(value) / 100.0, 4)
        node = node_map.get(field["node_id"])
        if node is not None:
            node["config"][field["config_key"]] = value

    # Initialize typed business_config if not present
    biz_cfg = definition.get("business_config") or BusinessConfigService.get_default_config_for_template(req.template_id)
    if req.template_id == "tpl_fnb_sales_to_marketing_v1":
        if "default_discount_pct" in req.onboarding:
            biz_cfg["max_discount_pct"] = float(req.onboarding["default_discount_pct"])
        if "min_drop_percentage" in req.onboarding:
            biz_cfg["sales_drop_threshold_pct"] = float(req.onboarding["min_drop_percentage"])
        if "promo_duration_days" in req.onboarding:
            biz_cfg["campaign_duration_days"] = int(req.onboarding["promo_duration_days"])
    elif req.template_id == "tpl_used_iphone_pricing_v1":
        if "target_gross_margin_pct" in req.onboarding:
            biz_cfg["target_profit_pct"] = float(req.onboarding["target_gross_margin_pct"])
        if "overhead_fee" in req.onboarding:
            biz_cfg["overhead_cost_idr"] = int(req.onboarding["overhead_fee"])
        if "rounding_increment" in req.onboarding:
            biz_cfg["rounding_increment_idr"] = int(req.onboarding["rounding_increment"])
        if "max_premium_pct" in req.onboarding:
            biz_cfg["market_deviation_threshold_pct"] = float(req.onboarding["max_premium_pct"])
    definition["business_config"] = biz_cfg

    wf = WorkflowDefinition.model_validate(definition)
    validation = WorkflowValidator.validate_workflow(wf)
    if not validation.is_valid:
        raise HTTPException(status_code=400, detail={"message": "Konfigurasi onboarding tidak valid.",
                                                     "errors": validation.errors})

    try:
        async with AsyncSessionLocal() as session:
            await WorkflowRepository.save_workflow(session, wf, is_active=False)
            summary = await _workflow_summary_db(session, wf_id)
    except Exception as exc:
        logger.error(f"Could not persist workflow {wf_id} to database: {exc}")
        raise HTTPException(status_code=500, detail=f"Gagal menyimpan workflow ke basis data: {exc}")

    return {"workflow": wf.model_dump(), "validation": validation.model_dump(), "summary": summary}


@router.get("/{workflow_id}", summary="Ringkasan workflow + daftar versi")
async def get_workflow(workflow_id: str) -> Dict[str, Any]:
    async with AsyncSessionLocal() as session:
        return await _workflow_summary_db(session, workflow_id)


@router.get("/{workflow_id}/runs", summary="Riwayat run alur kerja tertentu")
async def list_workflow_runs(workflow_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    return await list_runs(workflow_id=workflow_id, limit=limit)


@router.get("/{workflow_id}/versions/{version}", summary="Definisi kanonikal versi tertentu")
async def get_workflow_version(workflow_id: str, version: int) -> WorkflowDefinition:
    async with AsyncSessionLocal() as session:
        versions = await _get_versions_db(session, workflow_id)
        if version not in versions:
            raise HTTPException(status_code=404, detail=f"Versi {version} tidak ditemukan.")
        return versions[version]


@router.post("/{workflow_id}/proposals", summary="AI membuat usulan typed patch dari instruksi chat")
async def generate_proposal(workflow_id: str, req: ProposalRequest) -> Dict[str, Any]:
    async with AsyncSessionLocal() as session:
        versions = await _get_versions_db(session, workflow_id)
        active_wf = await WorkflowRepository.get_active_workflow(session, workflow_id)
        base_version = active_wf.version if active_wf else max(versions.keys())
        base_wf = versions[base_version]

        for v in [v for v in versions if v > base_version and versions[v].status == WorkflowStatus.DRAFT]:
            await WorkflowRepository.delete_draft_version(session, workflow_id, v)

        proposal: WorkflowProposal = await ProposalGenerator.generate_proposal(base_wf, req.instruction)
        prop_id = f"prop_{uuid.uuid4().hex[:8]}"

        if proposal.is_valid and proposal.patch_operations:
            candidate_wf, _ = PatchEngine.apply_patch(
                base_workflow=base_wf,
                operations=proposal.patch_operations,
                new_version=proposal.proposed_version,
                rationale=proposal.rationale
            )
            await WorkflowRepository.save_workflow(session, candidate_wf, is_active=False)

        await WorkflowRepository.save_patch_proposal(session, prop_id, workflow_id, proposal.model_dump())
        data = proposal.model_dump()
        data["proposal_id"] = prop_id
        return data


@router.post("/{workflow_id}/versions/{version}/reject", summary="Tolak draft; versi aktif tidak berubah")
async def reject_draft(workflow_id: str, version: int) -> Dict[str, Any]:
    async with AsyncSessionLocal() as session:
        versions = await _get_versions_db(session, workflow_id)
        wf = versions.get(version)
        if not wf:
            raise HTTPException(status_code=404, detail=f"Versi {version} tidak ditemukan.")
        if wf.status != WorkflowStatus.DRAFT:
            raise HTTPException(status_code=400, detail="Hanya versi DRAFT yang bisa ditolak.")
        active_wf = await WorkflowRepository.get_active_workflow(session, workflow_id)
        active_v = active_wf.version if active_wf else None
        if version == 1 and active_v is None:
            raise HTTPException(status_code=400, detail="Draft v1 adalah konfigurasi awal; revisi lewat chat atau hapus workflow.")
        await WorkflowRepository.delete_draft_version(session, workflow_id, version)
        return {
            "status": "REJECTED",
            "active_version": active_v,
            "message": f"Draft v{version} ditolak. Versi aktif tetap v{active_v}."
        }


@router.post("/{workflow_id}/validate", summary="Validasi DAG, allowlist tool, schema & permission")
async def validate_workflow_endpoint(workflow_id: str, version: Optional[int] = None) -> ValidationResult:
    async with AsyncSessionLocal() as session:
        versions = await _get_versions_db(session, workflow_id)
        wf = versions.get(version or max(versions.keys()))
        if not wf:
            raise HTTPException(status_code=404, detail=f"Versi {version} tidak ditemukan.")
        return WorkflowValidator.validate_workflow(wf, enforce_tool_allowlist=True)


@router.get("/templates/{template_id}/sample-data", summary="Mengambil data contoh input untuk template")
def get_template_sample_data(template_id: str) -> Dict[str, Any]:
    import os
    if template_id == "tpl_used_iphone_pricing_v1":
        path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "fixtures", "vendor_raw_samples.csv")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return {"format": "csv", "filename": "vendor_sample.csv", "content": f.read()}
    elif template_id == "tpl_fnb_sales_to_marketing_v1":
        pos_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "synthetic_pos_60days.csv")
        if os.path.exists(pos_path):
            with open(pos_path, "r", encoding="utf-8") as f:
                lines = [f.readline() for _ in range(30)]
                return {"format": "csv", "filename": "sample_pos_transactions.csv", "content": "".join(lines)}
        return {
            "format": "csv",
            "filename": "sample_pos_transactions.csv",
            "content": "transaction_id,occurred_at,product_id,product_name,category,quantity,unit_price,total_amount,payment_method\n"
        }
    return {"format": "text", "filename": "sample.txt", "content": ""}


@router.post("/{workflow_id}/dry-run", summary="Simulasi fixture sintetis tanpa side effect")
async def dry_run_endpoint(
    workflow_id: str,
    version: Optional[int] = None,
    req: Optional[LiveRunRequest] = None,
) -> DryRunResult:
    async with AsyncSessionLocal() as session:
        versions = await _get_versions_db(session, workflow_id)
        wf = versions.get(version or max(versions.keys()))
        if not wf:
            raise HTTPException(status_code=404, detail=f"Versi {version} tidak ditemukan.")
        csv = req.csv_content if req else None
        res = await WorkflowRunner.run_dry_run(wf, custom_csv_content=csv)

        try:
            await WorkflowRepository.save_run_record(
                session=session,
                run_id=f"dry_{uuid.uuid4().hex[:8]}",
                workflow_id=workflow_id,
                tenant_id=wf.tenant_id,
                version=wf.version,
                run_mode="DRY_RUN",
                status="COMPLETED" if res.is_clean else "FAILED",
                traces=res.node_traces,
                output={"summary": res.summary, "items_processed": res.items_processed},
                started_at=datetime.now(),
                completed_at=datetime.now()
            )
        except Exception as exc:
            logger.error(f"Failed to persist dry run to database: {exc}")
            raise HTTPException(status_code=500, detail=f"Gagal menyimpan hasil dry-run: {exc}")

        return res


@router.post("/{workflow_id}/approve", summary="Tier 1: Configuration Approval — aktifkan versi immutable")
async def approve_workflow_version(workflow_id: str, version: int) -> Dict[str, Any]:
    async with AsyncSessionLocal() as session:
        versions = await _get_versions_db(session, workflow_id)
        wf = versions.get(version)
        if not wf:
            raise HTTPException(status_code=404, detail=f"Versi {version} tidak ditemukan.")

        val = WorkflowValidator.validate_workflow(wf)
        if not val.is_valid:
            raise HTTPException(status_code=400, detail={"message": "Gagal validasi, tidak dapat diaktifkan.",
                                                         "errors": val.errors})

        active_wf = await WorkflowRepository.get_active_workflow(session, workflow_id)
        previous = active_wf.version if active_wf else None
        wf.status = WorkflowStatus.ACTIVE
        wf.activated_at = datetime.now().isoformat()

        try:
            await WorkflowRepository.save_workflow(session, wf, is_active=True)
        except Exception as exc:
            logger.error(f"Could not persist approved workflow {workflow_id} to database: {exc}")
            raise HTTPException(status_code=500, detail=f"Gagal menyimpan persetujuan workflow: {exc}")

        return {
            "status": "SUCCESS",
            "message": f"Versi {version} disetujui dan ACTIVE." + (f" Versi {previous} diarsipkan." if previous else ""),
            "active_version": version
        }


@router.post("/{workflow_id}/runs", summary="Menjalankan versi ACTIVE dengan data vendor")
async def start_workflow_run(workflow_id: str, req: LiveRunRequest) -> Dict[str, Any]:
    async with AsyncSessionLocal() as session:
        await _get_versions_db(session, workflow_id)
        active_wf = await WorkflowRepository.get_active_workflow(session, workflow_id)
        if not active_wf:
            raise HTTPException(
                status_code=400,
                detail="Belum ada versi ACTIVE. Lakukan dry-run lalu Configuration Approval terlebih dahulu."
            )

        run_id = f"run_{uuid.uuid4().hex[:8]}"
        start_time = datetime.now()
        run_status, node_traces, pending_action, final_output = await WorkflowRunner.run_live(
            workflow=active_wf, run_id=run_id, csv_content=req.csv_content, db_session=session
        )

        try:
            await WorkflowRepository.save_run_record(
                session=session,
                run_id=run_id,
                workflow_id=workflow_id,
                tenant_id=active_wf.tenant_id,
                version=active_wf.version,
                run_mode="LIVE",
                status=run_status.value if hasattr(run_status, "value") else str(run_status),
                traces=node_traces,
                pending_action=pending_action,
                output=final_output,
                csv_content=req.csv_content,
                started_at=start_time,
                completed_at=datetime.now() if run_status == RunStatus.COMPLETED else None
            )
        except Exception as exc:
            logger.error(f"Could not persist run {run_id} to database: {exc}")
            raise HTTPException(status_code=500, detail=f"Gagal menyimpan eksekusi run: {exc}")

        return {
            "run_id": run_id,
            "workflow_id": workflow_id,
            "workflow_name": active_wf.name,
            "version": active_wf.version,
            "status": run_status,
            "started_at": start_time.isoformat(),
            "csv_content": req.csv_content,
            "traces": [t.model_dump() for t in node_traces],
            "pending_action": pending_action.model_dump() if pending_action else None,
            "output": final_output.model_dump() if hasattr(final_output, "model_dump") else None,
        }


# =====================================================================
# BUSINESS CONFIGURATION & PROPOSALS (PHASE 2 LEVEL 1)
# =====================================================================

class BusinessProposalRequest(BaseModel):
    instruction: Optional[str] = None
    changes: Optional[List[ConfigPatchOperation]] = None
    reviewer_id: str = "business_owner"


class BusinessProposalDecision(BaseModel):
    proposal_id: str
    reviewer_id: str = "business_owner"
    reason: Optional[str] = None


@router.get("/{workflow_id}/config", summary="Mendapatkan konfigurasi bisnis typed alur kerja aktif")
async def get_workflow_business_config(workflow_id: str) -> Dict[str, Any]:
    async with AsyncSessionLocal() as session:
        wf = await WorkflowRepository.get_active_workflow(session, workflow_id)
        if not wf:
            versions = await WorkflowRepository.get_all_versions_dict(session, workflow_id)
            if versions:
                wf = versions[max(versions.keys())]

        if not wf:
            raise HTTPException(status_code=404, detail=f"Alur kerja '{workflow_id}' tidak ditemukan.")

        cfg = wf.business_config or BusinessConfigService.get_default_config_for_template(wf.template_id)
        return {
            "workflow_id": wf.id,
            "template_id": wf.template_id,
            "version": wf.version,
            "status": wf.status,
            "business_config": cfg
        }


@router.post("/{workflow_id}/config/propose", summary="Membuat proposal perubahan konfigurasi bisnis")
async def propose_business_config(workflow_id: str, req: BusinessProposalRequest) -> BusinessProposal:
    async with AsyncSessionLocal() as session:
        wf = await WorkflowRepository.get_active_workflow(session, workflow_id)
        if not wf:
            versions = await WorkflowRepository.get_all_versions_dict(session, workflow_id)
            if versions:
                wf = versions[max(versions.keys())]

        if not wf:
            raise HTTPException(status_code=404, detail=f"Alur kerja '{workflow_id}' tidak ditemukan.")

        if req.instruction:
            proposal = BusinessConfigService.generate_proposal_for_workflow(wf, req.instruction)
        elif req.changes:
            is_valid, errs, candidate_cfg, diff_sum = BusinessConfigService.validate_and_apply_patch(
                workflow=wf,
                changes=req.changes,
                base_version=wf.version
            )
            prop_id = f"prop_cfg_{uuid.uuid4().hex[:6]}"
            proposal = BusinessProposal(
                proposal_id=prop_id,
                intent="DIRECT_PATCH_MODIFICATION",
                workflow_id=wf.id,
                base_version=wf.version,
                proposed_version=wf.version + 1,
                changes=req.changes,
                explanation="Perubahan konfigurasi parameter bisnis diajukan secara terstruktur.",
                diff_summary=diff_sum,
                is_valid=is_valid,
                validation_errors=errs,
                status=ProposalStatus.VALIDATED if is_valid else ProposalStatus.PROPOSED,
                created_at=datetime.now().isoformat(),
                candidate_config=candidate_cfg if is_valid else None
            )
        else:
            raise HTTPException(status_code=400, detail="Harap sediakan 'instruction' teks atau daftar 'changes'.")

        await WorkflowRepository.save_business_proposal(session, proposal)
        return proposal


@router.post("/{workflow_id}/config/approve", summary="Setujui proposal & aktifkan versi immutable baru")
async def approve_business_config(workflow_id: str, req: BusinessProposalDecision) -> Dict[str, Any]:
    try:
        async with AsyncSessionLocal() as session:
            new_wf, proposal = await BusinessConfigService.approve_proposal(
                session=session,
                workflow_id=workflow_id,
                proposal_id=req.proposal_id,
                reviewer_id=req.reviewer_id
            )
            return {
                "status": "APPROVED",
                "message": f"Proposal '{req.proposal_id}' disetujui. Versi aktif alur kerja kini v{new_wf.version}.",
                "workflow": new_wf.model_dump(),
                "proposal": proposal.model_dump()
            }
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as exc:
        logger.error(f"Error approving business config: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Gagal menyetujui konfigurasi: {str(exc)}")


@router.post("/{workflow_id}/config/reject", summary="Tolak proposal konfigurasi bisnis")
async def reject_business_config(workflow_id: str, req: BusinessProposalDecision) -> Dict[str, Any]:
    try:
        async with AsyncSessionLocal() as session:
            proposal = await BusinessConfigService.reject_proposal(
                session=session,
                proposal_id=req.proposal_id,
                reviewer_id=req.reviewer_id,
                reason=req.reason
            )
            return {
                "status": "REJECTED",
                "message": f"Proposal '{req.proposal_id}' ditolak. Versi alur kerja tidak berubah.",
                "proposal": proposal.model_dump()
            }
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))

