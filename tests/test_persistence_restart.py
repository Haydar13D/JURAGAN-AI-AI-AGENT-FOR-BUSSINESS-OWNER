"""
tests/test_persistence_restart.py
T9 — Hapus in-memory store, semua lewat repository.
Memverifikasi:
1. Proposal bisnis tetap ada setelah session ditutup dan dibuka session baru ("restart").
2. Detail run workflow tetap ada di database setelah session baru dibuka.
"""

import json
import uuid
from datetime import datetime

import pytest

from app.core.database import AsyncSessionLocal, init_db
from app.schemas.workflow import (
    AssistantMessageRequest,
    NodeRunEvent,
    RunStatus,
    WorkflowDefinition,
    WorkflowStatus,
)
from app.services.business_config_service import BusinessConfigService
from app.services.workflow_repository import WorkflowRepository


@pytest.mark.asyncio
async def test_business_proposal_persists_across_sessions():
    await init_db()
    wf_id = f"wf_persist_{uuid.uuid4().hex[:8]}"

    with open("data/fixtures/fnb_marketing_template.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    data["id"] = wf_id
    data["version"] = 1
    data["status"] = "ACTIVE"
    wf = WorkflowDefinition.model_validate(data)

    # Session 1: simpan workflow & buat proposal lewat assistant
    async with AsyncSessionLocal() as s1:
        await WorkflowRepository.save_workflow(s1, wf, is_active=True)
        resp = await BusinessConfigService.handle_assistant_message(
            s1,
            AssistantMessageRequest(
                message="Ubah maksimal diskon menjadi 17%",
                workflow_id=wf_id,
                tenant_id=wf.tenant_id,
            ),
        )
        assert resp.proposal is not None
        prop_id = resp.proposal.proposal_id

    # Session 2 ("restart"): proposal harus tetap bisa diambil dari DB
    async with AsyncSessionLocal() as s2:
        loaded = await BusinessConfigService.get_proposal(s2, prop_id)
        assert loaded is not None
        assert loaded.proposal_id == prop_id
        assert loaded.workflow_id == wf_id
        assert loaded.diff_summary["max_discount_pct"]["after"] == 17.0


@pytest.mark.asyncio
async def test_run_detail_persists_across_sessions():
    await init_db()
    wf_id = f"wf_run_persist_{uuid.uuid4().hex[:8]}"
    run_id = f"run_persist_{uuid.uuid4().hex[:8]}"

    with open("data/fixtures/fnb_marketing_template.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    data["id"] = wf_id
    data["version"] = 1
    data["status"] = "ACTIVE"
    wf = WorkflowDefinition.model_validate(data)

    now = datetime.now()
    trace = NodeRunEvent(
        node_id="ingest_pos_data",
        tool_type="fnb.ingest_pos_data",
        status=RunStatus.COMPLETED,
        started_at=now.isoformat(),
        completed_at=now.isoformat(),
        output_snapshot={"status": "OK"},
        execution_time_ms=12.5,
    )

    async with AsyncSessionLocal() as s1:
        await WorkflowRepository.save_workflow(s1, wf, is_active=True)
        await WorkflowRepository.save_run_record(
            session=s1,
            run_id=run_id,
            workflow_id=wf_id,
            tenant_id=wf.tenant_id,
            version=1,
            run_mode="LIVE",
            status="COMPLETED",
            traces=[trace],
            output={"ok": True},
            started_at=now,
            completed_at=now,
        )

    async with AsyncSessionLocal() as s2:
        detail = await WorkflowRepository.get_run_detail(s2, run_id)
        assert detail is not None
        assert detail["run_id"] == run_id
        assert detail["workflow_id"] == wf_id
        assert detail["status"] == "COMPLETED"
        assert len(detail["traces"]) == 1
        assert detail["traces"][0]["node_id"] == "ingest_pos_data"
