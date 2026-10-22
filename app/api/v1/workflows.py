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
from fastapi import APIRouter, HTTPException
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
)
from app.core.tool_registry import ToolRegistry
from app.core.logging import logger
from app.core.database import AsyncSessionLocal
from app.services.workflow_repository import WorkflowRepository
from app.services.workflow_validator import WorkflowValidator, ValidationResult
from app.services.patch_engine import PatchEngine
from app.services.proposal_generator import ProposalGenerator
from app.services.workflow_runner import WorkflowRunner
from app.services.retail_pricing import register_retail_tools
from app.services.fnb_marketing import register_fnb_tools
from app.services import template_catalog

router = APIRouter(prefix="/workflows", tags=["Kirata Workflow Builder"])

register_retail_tools()
register_fnb_tools()

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
        return {
            "format": "pos_summary",
            "filename": "pos_sample.json",
            "content": json.dumps({"outlet_id": 1, "days": 30, "sample_note": "Data POS transaksi tersimpan di database"})
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
