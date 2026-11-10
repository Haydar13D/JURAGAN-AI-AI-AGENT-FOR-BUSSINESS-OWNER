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

# In-memory storage for MVP PoC (to be swapped with SQLAlchemy models in app/models/workflow.py)
_WORKFLOWS_STORE: Dict[str, Dict[int, WorkflowDefinition]] = {}  # {workflow_id: {version: definition}}
_ACTIVE_VERSION: Dict[str, Optional[int]] = {}
_WORKFLOW_META: Dict[str, Dict[str, Any]] = {}
_PROPOSALS_STORE: Dict[str, Dict[str, Any]] = {}
_RUNS_STORE: Dict[str, Dict[str, Any]] = {}
_PENDING_ACTIONS_STORE: Dict[str, ActionApprovalRequest] = {}


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

def _get_versions(workflow_id: str) -> Dict[int, WorkflowDefinition]:
    versions = _WORKFLOWS_STORE.get(workflow_id)
    if not versions:
        raise HTTPException(status_code=404, detail=f"Alur kerja '{workflow_id}' tidak ditemukan.")
    return versions


def _workflow_summary(workflow_id: str) -> Dict[str, Any]:
    versions = _WORKFLOWS_STORE[workflow_id]
    latest = max(versions.keys())
    meta = _WORKFLOW_META.get(workflow_id, {})
    return {
        "id": workflow_id,
        "name": versions[latest].name,
        "template_id": versions[latest].template_id,
        "tenant_id": versions[latest].tenant_id,
        "active_version": _ACTIVE_VERSION.get(workflow_id),
        "latest_version": latest,
        "versions": [
            {"version": v, "status": wf.status} for v, wf in sorted(versions.items())
        ],
        "created_at": meta.get("created_at"),
        "run_count": sum(1 for r in _RUNS_STORE.values() if r["workflow_id"] == workflow_id),
    }


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
        wf = card.workflow
        _WORKFLOWS_STORE[wf.id] = {wf.version: wf}
        _ACTIVE_VERSION[wf.id] = None
        _WORKFLOW_META[wf.id] = {"created_at": datetime.now().isoformat()}
    return card


@router.post("/parse-file", summary="Parse file POS (Excel .xlsx / CSV) menjadi data transaksi standar")
async def parse_pos_file_endpoint(file: UploadFile = File(...)) -> Dict[str, Any]:
    """
    Parses uploaded POS data file (.xlsx, .xls, .csv).
    Detects custom multi-table layouts (e.g. Daily Report with Menu & Jumlah),
    standard tables, and returns clean CSV text along with detected product names.
    """
    import io
    import re
    import pandas as pd
    
    filename = file.filename or "upload.csv"
    content_bytes = await file.read()
    
    detected_products = []
    generated_csv_lines = [
        "transaction_id,occurred_at,product_id,product_name,category,quantity,unit_price,total_amount,payment_method"
    ]
    
    if filename.lower().endswith((".xlsx", ".xls")):
        try:
            xl = pd.ExcelFile(io.BytesIO(content_bytes))
            for sheet in xl.sheet_names:
                df = xl.parse(sheet)
                menu_col = None
                for col in df.columns:
                    if "menu" in str(col).lower():
                        menu_col = col
                        break
                if menu_col is not None:
                    cols_list = list(df.columns)
                    menu_idx = cols_list.index(menu_col)
                    amt_col = None
                    for c in cols_list[menu_idx + 1:]:
                        if any(k in str(c).lower() for k in ["jumlah", "omzet", "total", "harga", "pendapatan"]):
                            amt_col = c
                            break
                    if not amt_col:
                        amt_col = cols_list[menu_idx + 1] if len(cols_list) > menu_idx + 1 else None

                    for idx, row in df.iterrows():
                        val = row[menu_col]
                        if pd.notna(val) and str(val).strip():
                            m_name = str(val).strip()
                            if m_name.lower() in ["menu", "total", "jumlah", "grand total", "total transaksi"]:
                                continue
                            price = 20000.0
                            if amt_col and pd.notna(row[amt_col]):
                                try:
                                    c_str = re.sub(r"[^\d.]", "", str(row[amt_col]))
                                    price = float(c_str) if c_str else 20000.0
                                except Exception:
                                    price = 20000.0
                            is_drink = any(w in m_name.lower() for w in [
                                "kopi", "coffee", "latte", "macchiato", "cappuccino", "espresso", "americano",
                                "mocha", "matcha", "tea", "teh", "drink", "jus", "juice", "es ", "es_", "boba",
                                "beverage", "soda", "syrup", "sirup", "milk", "susu", "frappe", "smoothie",
                                "mocktail", "cocktail", "cooler", "non-kopi", "minuman", "wedang", "thai tea", "lemonade"
                            ])
                            cat = "Minuman" if is_drink else "Makanan"
                            detected_products.append(m_name)
                            generated_csv_lines.append(
                                f"TXN-{1000 + len(detected_products)},2026-10-13 14:00:00,PROD-{len(detected_products):03d},{m_name},{cat},10,{int(price)},{int(price * 10)},QRIS"
                            )
            
            # If standard single-table format
            if not detected_products:
                df = pd.read_excel(io.BytesIO(content_bytes))
                return {
                    "success": True,
                    "filename": filename,
                    "csv_content": df.to_csv(index=False),
                    "total_rows": len(df),
                    "detected_products": [str(c) for c in df.iloc[:, 0].head(5)],
                    "message": f"Berhasil membaca file Excel {filename} ({len(df)} baris)."
                }
        except Exception as exc:
            logger.error(f"Error parsing Excel file {filename}: {exc}")
            raise HTTPException(status_code=400, detail=f"Gagal membaca file Excel: {str(exc)}")
    else:
        # CSV handling
        try:
            text = content_bytes.decode("utf-8", errors="replace")
            # Check if multi-table format
            if "Menu" in text and ("Daily Report" in text or "Dashboard" in text or "Tanggal" in text):
                lines = [line.split(",") for line in text.strip().split("\n")]
                header_idx = -1
                header_line = None
                for l_idx, line in enumerate(lines):
                    clean = [c.strip().strip('"') for c in line]
                    if "Menu" in clean:
                        header_idx = l_idx
                        header_line = clean
                        break
                if header_line and "Menu" in header_line:
                    m_idx = header_line.index("Menu")
                    a_idx = m_idx + 1
                    for line in lines[header_idx + 1:]:
                        clean = [c.strip().strip('"') for c in line]
                        if len(clean) > m_idx and clean[m_idx]:
                            m_name = clean[m_idx]
                            if m_name.lower() in ["menu", "total", "jumlah", "", "grand total"]:
                                continue
                            price_raw = clean[a_idx] if len(clean) > a_idx else "20000"
                            try:
                                price = float(re.sub(r"[^\d.]", "", price_raw)) if price_raw else 20000.0
                            except Exception:
                                price = 20000.0
                            is_drink = any(w in m_name.lower() for w in [
                                "kopi", "coffee", "latte", "macchiato", "cappuccino", "espresso", "americano",
                                "mocha", "matcha", "tea", "teh", "drink", "jus", "juice", "es ", "es_", "boba",
                                "beverage", "soda", "syrup", "sirup", "milk", "susu", "frappe", "smoothie",
                                "mocktail", "cocktail", "cooler", "non-kopi", "minuman", "wedang", "thai tea", "lemonade"
                            ])
                            cat = "Minuman" if is_drink else "Makanan"
                            detected_products.append(m_name)
                            generated_csv_lines.append(
                                f"TXN-{1000 + len(detected_products)},2026-10-13 14:00:00,PROD-{len(detected_products):03d},{m_name},{cat},10,{int(price)},{int(price * 10)},QRIS"
                            )
            if not detected_products:
                return {
                    "success": True,
                    "filename": filename,
                    "csv_content": text,
                    "total_rows": len(text.strip().split("\n")),
                    "detected_products": [],
                    "message": f"File CSV '{filename}' dimuat."
                }
        except Exception as exc:
            logger.error(f"Error parsing CSV file {filename}: {exc}")
            raise HTTPException(status_code=400, detail=f"Gagal membaca file CSV: {str(exc)}")

    clean_csv = "\n".join(generated_csv_lines)
    return {
        "success": True,
        "filename": filename,
        "csv_content": clean_csv,
        "total_rows": len(detected_products),
        "detected_products": detected_products,
        "message": f"Berhasil mengekstrak {len(detected_products)} menu dari file {filename}: {', '.join(detected_products)}"
    }


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
def list_workflows(tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
    items = [_workflow_summary(wid) for wid in _WORKFLOWS_STORE]
    if tenant_id:
        items = [i for i in items if i["tenant_id"] == tenant_id]
    return sorted(items, key=lambda i: i.get("created_at") or "", reverse=True)


@router.get("/runs", summary="Riwayat run semua workflow")
async def list_runs(
    tenant_id: Optional[str] = None,
    workflow_id: Optional[str] = None,
    status: Optional[str] = None,
    run_mode: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    db_runs = []
    try:
        async with AsyncSessionLocal() as session:
            db_runs = await WorkflowRepository.list_runs(
                session, tenant_id=tenant_id, workflow_id=workflow_id,
                status=status, run_mode=run_mode, limit=limit
            )
    except Exception as exc:
        logger.warning(f"Error querying runs from database: {exc}")

    # Merge with memory store
    seen_ids = {r["run_id"] for r in db_runs}
    for r in _RUNS_STORE.values():
        if r["run_id"] not in seen_ids:
            if not workflow_id or r.get("workflow_id") == workflow_id:
                if not status or r.get("status") == status:
                    if not run_mode or r.get("run_mode", "LIVE") == run_mode:
                        db_runs.append({k: v for k, v in r.items() if k not in ("traces", "output")})
    return sorted(db_runs, key=lambda r: r.get("started_at") or "", reverse=True)


@router.get("/runs/{run_id}/events", summary="Timeline event eksekusi sebuah run beserta memory snapshot")
async def get_run_events(run_id: str) -> Dict[str, Any]:
    try:
        async with AsyncSessionLocal() as session:
            detail = await WorkflowRepository.get_run_detail(session, run_id)
            if detail:
                if run_id in _RUNS_STORE and _RUNS_STORE[run_id].get("pending_action"):
                    detail["pending_action"] = _RUNS_STORE[run_id]["pending_action"]
                return detail
    except Exception as exc:
        logger.warning(f"Error querying run detail from database: {exc}")

    run = _RUNS_STORE.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' tidak ditemukan.")
    return run


@router.post("/runs/{run_id}/actions/{action_id}/decision",
             summary="Tier 2: Business Action Approval — setujui/tolak ekspor katalog")
async def decide_business_action(run_id: str, action_id: str, req: ActionDecisionRequest) -> Dict[str, Any]:
    run = _RUNS_STORE.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' tidak ditemukan.")

    pending = _PENDING_ACTIONS_STORE.get(action_id)
    if not pending or pending.run_id != run_id:
        raise HTTPException(status_code=404, detail=f"Pending action '{action_id}' tidak ditemukan untuk run ini.")

    if req.decision == "REJECT":
        run["status"] = RunStatus.REJECTED
        run["pending_action"] = None
        del _PENDING_ACTIONS_STORE[action_id]

        try:
            async with AsyncSessionLocal() as session:
                await WorkflowRepository.record_action_decision(
                    session=session,
                    run_id=run_id,
                    action_id=action_id,
                    decision="REJECT",
                    reviewer_id=req.reviewer_user_id,
                    decision_reason=req.decision_reason
                )
        except Exception as exc:
            logger.warning(f"Failed to record rejection in database: {exc}")

        return {
            "status": "REJECTED",
            "message": f"Aksi bisnis ditolak oleh {req.reviewer_user_id}. Tidak ada data yang dipublikasikan/diekspor."
        }

    # Version pinned at run start
    wf = _WORKFLOWS_STORE[run["workflow_id"]][run["version"]]
    run_status, node_traces, _, final_out = await WorkflowRunner.run_live(
        workflow=wf,
        run_id=run_id,
        csv_content=run.get("csv_content"),
        action_approval_granted=True,
        reviewer_id=req.reviewer_user_id
    )

    run["status"] = run_status
    run["traces"] = [t.model_dump() for t in node_traces]
    run["output"] = final_out.model_dump() if hasattr(final_out, "model_dump") else None
    run["pending_action"] = None
    run["approved_by"] = req.reviewer_user_id
    del _PENDING_ACTIONS_STORE[action_id]

    try:
        async with AsyncSessionLocal() as session:
            await WorkflowRepository.record_action_decision(
                session=session,
                run_id=run_id,
                action_id=action_id,
                decision="APPROVE",
                reviewer_id=req.reviewer_user_id,
                decision_reason=req.decision_reason,
                final_output=run["output"],
                updated_traces=node_traces
            )
    except Exception as exc:
        logger.warning(f"Failed to record approval decision in database: {exc}")

    return {
        "status": "APPROVED_AND_COMPLETED",
        "message": f"Aksi bisnis disetujui oleh {req.reviewer_user_id}.",
        "result": run["output"]
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

    _WORKFLOWS_STORE[wf_id] = {1: wf}
    _ACTIVE_VERSION[wf_id] = None
    _WORKFLOW_META[wf_id] = {"created_at": definition["created_at"]}

    try:
        async with AsyncSessionLocal() as session:
            await WorkflowRepository.save_workflow(session, wf, is_active=False)
    except Exception as exc:
        logger.warning(f"Could not persist workflow {wf_id} to database: {exc}")

    return {"workflow": wf.model_dump(), "validation": validation.model_dump(), "summary": _workflow_summary(wf_id)}


@router.get("/{workflow_id}", summary="Ringkasan workflow + daftar versi")
def get_workflow(workflow_id: str) -> Dict[str, Any]:
    _get_versions(workflow_id)
    return _workflow_summary(workflow_id)


@router.get("/{workflow_id}/runs", summary="Riwayat run alur kerja tertentu")
async def list_workflow_runs(workflow_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    return await list_runs(workflow_id=workflow_id, limit=limit)


@router.get("/{workflow_id}/versions/{version}", summary="Definisi kanonikal versi tertentu")
def get_workflow_version(workflow_id: str, version: int) -> WorkflowDefinition:
    versions = _get_versions(workflow_id)
    if version not in versions:
        raise HTTPException(status_code=404, detail=f"Versi {version} tidak ditemukan.")
    return versions[version]


@router.post("/{workflow_id}/proposals", summary="AI membuat usulan typed patch dari instruksi chat")
async def generate_proposal(workflow_id: str, req: ProposalRequest) -> Dict[str, Any]:
    versions = _get_versions(workflow_id)
    # Proposals are always based on the active version (or v1 draft if nothing is active yet)
    base_version = _ACTIVE_VERSION.get(workflow_id) or max(versions.keys())
    base_wf = versions[base_version]

    # Discard previous un-approved drafts newer than base
    for v in [v for v in versions if v > base_version and versions[v].status == WorkflowStatus.DRAFT]:
        del versions[v]

    proposal: WorkflowProposal = await ProposalGenerator.generate_proposal(base_wf, req.instruction)
    prop_id = f"prop_{uuid.uuid4().hex[:8]}"

    if proposal.is_valid and proposal.patch_operations:
        candidate_wf, _ = PatchEngine.apply_patch(
            base_workflow=base_wf,
            operations=proposal.patch_operations,
            new_version=proposal.proposed_version,
            rationale=proposal.rationale
        )
        versions[proposal.proposed_version] = candidate_wf

    _PROPOSALS_STORE[prop_id] = {"workflow_id": workflow_id, "proposal": proposal}
    data = proposal.model_dump()
    data["proposal_id"] = prop_id
    return data


@router.post("/{workflow_id}/versions/{version}/reject", summary="Tolak draft; versi aktif tidak berubah")
def reject_draft(workflow_id: str, version: int) -> Dict[str, Any]:
    versions = _get_versions(workflow_id)
    wf = versions.get(version)
    if not wf:
        raise HTTPException(status_code=404, detail=f"Versi {version} tidak ditemukan.")
    if wf.status != WorkflowStatus.DRAFT:
        raise HTTPException(status_code=400, detail="Hanya versi DRAFT yang bisa ditolak.")
    if version == 1 and _ACTIVE_VERSION.get(workflow_id) is None:
        raise HTTPException(status_code=400, detail="Draft v1 adalah konfigurasi awal; revisi lewat chat atau hapus workflow.")
    del versions[version]
    return {"status": "REJECTED", "active_version": _ACTIVE_VERSION.get(workflow_id),
            "message": f"Draft v{version} ditolak. Versi aktif tetap v{_ACTIVE_VERSION.get(workflow_id)}."}


@router.post("/{workflow_id}/validate", summary="Validasi DAG, allowlist tool, schema & permission")
def validate_workflow_endpoint(workflow_id: str, version: Optional[int] = None) -> ValidationResult:
    versions = _get_versions(workflow_id)
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
    versions = _get_versions(workflow_id)
    wf = versions.get(version or max(versions.keys()))
    if not wf:
        raise HTTPException(status_code=404, detail=f"Versi {version} tidak ditemukan.")
    csv = req.csv_content if req else None
    res = await WorkflowRunner.run_dry_run(wf, custom_csv_content=csv)

    # Persist dry run trace in memory logging
    try:
        async with AsyncSessionLocal() as session:
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
        logger.warning(f"Failed to persist dry run to database: {exc}")

    return res


@router.post("/{workflow_id}/approve", summary="Tier 1: Configuration Approval — aktifkan versi immutable")
async def approve_workflow_version(workflow_id: str, version: int) -> Dict[str, Any]:
    versions = _get_versions(workflow_id)
    wf = versions.get(version)
    if not wf:
        raise HTTPException(status_code=404, detail=f"Versi {version} tidak ditemukan.")

    val = WorkflowValidator.validate_workflow(wf)
    if not val.is_valid:
        raise HTTPException(status_code=400, detail={"message": "Gagal validasi, tidak dapat diaktifkan.",
                                                     "errors": val.errors})

    previous = _ACTIVE_VERSION.get(workflow_id)
    if previous and previous in versions:
        versions[previous].status = WorkflowStatus.ARCHIVED
    wf.status = WorkflowStatus.ACTIVE
    wf.activated_at = datetime.now().isoformat()
    _ACTIVE_VERSION[workflow_id] = version

    try:
        async with AsyncSessionLocal() as session:
            await WorkflowRepository.save_workflow(session, wf, is_active=True)
    except Exception as exc:
        logger.warning(f"Could not persist approved workflow {workflow_id} to database: {exc}")

    return {
        "status": "SUCCESS",
        "message": f"Versi {version} disetujui dan ACTIVE." + (f" Versi {previous} diarsipkan." if previous else ""),
        "active_version": version
    }


@router.post("/{workflow_id}/runs", summary="Menjalankan versi ACTIVE dengan data vendor")
async def start_workflow_run(workflow_id: str, req: LiveRunRequest) -> Dict[str, Any]:
    versions = _get_versions(workflow_id)
    active_v = _ACTIVE_VERSION.get(workflow_id)
    if not active_v:
        raise HTTPException(
            status_code=400,
            detail="Belum ada versi ACTIVE. Lakukan dry-run lalu Configuration Approval terlebih dahulu."
        )
    active_wf = versions[active_v]

    run_id = f"run_{uuid.uuid4().hex[:8]}"
    start_time = datetime.now()
    run_status, node_traces, pending_action, final_output = await WorkflowRunner.run_live(
        workflow=active_wf, run_id=run_id, csv_content=req.csv_content
    )
    if pending_action:
        _PENDING_ACTIONS_STORE[pending_action.action_id] = pending_action

    _RUNS_STORE[run_id] = {
        "run_id": run_id,
        "workflow_id": workflow_id,
        "workflow_name": active_wf.name,
        "version": active_v,
        "status": run_status,
        "started_at": start_time.isoformat(),
        "csv_content": req.csv_content,
        "traces": [t.model_dump() for t in node_traces],
        "pending_action": pending_action.model_dump() if pending_action else None,
        "output": final_output.model_dump() if hasattr(final_output, "model_dump") else None,
    }

    try:
        async with AsyncSessionLocal() as session:
            await WorkflowRepository.save_run_record(
                session=session,
                run_id=run_id,
                workflow_id=workflow_id,
                tenant_id=active_wf.tenant_id,
                version=active_v,
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
        logger.warning(f"Could not persist run {run_id} to database: {exc}")

    return _RUNS_STORE[run_id]


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
    wf = None
    try:
        async with AsyncSessionLocal() as session:
            wf = await WorkflowRepository.get_active_workflow(session, workflow_id)
    except Exception:
        pass

    if not wf:
        versions = _WORKFLOWS_STORE.get(workflow_id)
        if versions:
            active_v = _ACTIVE_VERSION.get(workflow_id) or max(versions.keys())
            wf = versions[active_v]

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
    wf = None
    versions = _WORKFLOWS_STORE.get(workflow_id)
    if versions:
        active_v = _ACTIVE_VERSION.get(workflow_id) or max(versions.keys())
        wf = versions[active_v]
    else:
        async with AsyncSessionLocal() as session:
            wf = await WorkflowRepository.get_active_workflow(session, workflow_id)

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
        BusinessConfigService._PROPOSALS[prop_id] = proposal
    else:
        raise HTTPException(status_code=400, detail="Harap sediakan 'instruction' teks atau daftar 'changes'.")

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
            # Synchronize memory store
            if workflow_id not in _WORKFLOWS_STORE:
                _WORKFLOWS_STORE[workflow_id] = {}
            _WORKFLOWS_STORE[workflow_id][new_wf.version] = new_wf
            _ACTIVE_VERSION[workflow_id] = new_wf.version

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
def reject_business_config(workflow_id: str, req: BusinessProposalDecision) -> Dict[str, Any]:
    try:
        proposal = BusinessConfigService.reject_proposal(
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

