"""
tests/test_workflow_memory.py
Tests persistent execution memory, node snapshots, and history logging in Kirata AI Workflow Builder.
"""

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, func

from app.main import app
from app.core.database import AsyncSessionLocal
from app.models.workflow import WorkflowRun, NodeRunEvent, ActionApproval

BASE = "/api/v1/workflows"


@pytest.mark.asyncio
async def test_workflow_execution_memory_persistence():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Create a workflow from template
        r = await ac.post(f"{BASE}/from-template", json={
            "template_id": "tpl_used_iphone_pricing_v1",
            "name": "Memory Test Store",
            "onboarding": {"target_gross_margin_pct": 14}
        })
        assert r.status_code == 200
        wf = r.json()["workflow"]
        wf_id = wf["id"]

        # 2. Run dry run -> verifies dry run trace is recorded in database
        dry = (await ac.post(f"{BASE}/{wf_id}/dry-run?version=1")).json()
        assert dry["is_clean"] is True

        # 3. Approve configuration v1
        appr = await ac.post(f"{BASE}/{wf_id}/approve?version=1")
        assert appr.status_code == 200

        # 4. Start live run -> pauses at WAITING_APPROVAL
        run_res = (await ac.post(f"{BASE}/{wf_id}/runs", json={})).json()
        assert run_res["status"] == "WAITING_APPROVAL"
        run_id = run_res["run_id"]
        action_id = run_res["pending_action"]["action_id"]

        # Verify that SQLite database has recorded this run and node traces
        async with AsyncSessionLocal() as session:
            run_db = await session.get(WorkflowRun, run_id)
            assert run_db is not None
            assert run_db.status == "WAITING_APPROVAL"
            assert run_db.run_mode == "LIVE"

            # Check node run events count in db
            ev_stmt = select(NodeRunEvent).where(NodeRunEvent.run_id == run_id)
            ev_res = await session.execute(ev_stmt)
            events = ev_res.scalars().all()
            assert len(events) >= 7  # nodes executed up to approval gate

            # Check that input and output snapshots are stored in memory
            pricing_ev = next(e for e in events if e.node_id == "calculate_pricing")
            assert pricing_ev.input_snapshot_json is not None
            assert pricing_ev.output_snapshot_json is not None
            assert "items" in pricing_ev.input_snapshot_json and "items" in pricing_ev.output_snapshot_json

            # Check pending action approval in db
            act_db = await session.get(ActionApproval, action_id)
            assert act_db is not None
            assert act_db.status == "PENDING"

        # 5. Approve the business action
        decision_res = (await ac.post(
            f"{BASE}/runs/{run_id}/actions/{action_id}/decision",
            json={"decision": "APPROVE", "reviewer_user_id": "test_owner_01", "decision_reason": "Pricing verified"}
        )).json()
        assert decision_res["status"] == "APPROVED_AND_COMPLETED"

        # Verify database is updated post-approval
        async with AsyncSessionLocal() as session:
            run_db = await session.get(WorkflowRun, run_id)
            assert run_db.status == "COMPLETED"
            assert run_db.completed_at is not None

            act_db = await session.get(ActionApproval, action_id)
            assert act_db.status == "APPROVE"
            assert act_db.reviewed_by == "test_owner_01"

        # 6. Test GET /runs API endpoint with filtering
        all_runs = (await ac.get(f"{BASE}/runs")).json()
        assert any(r["run_id"] == run_id for r in all_runs)

        filtered_live = (await ac.get(f"{BASE}/runs?run_mode=LIVE")).json()
        assert any(r["run_id"] == run_id for r in filtered_live)

        filtered_wf = (await ac.get(f"{BASE}/{wf_id}/runs")).json()
        assert any(r["run_id"] == run_id for r in filtered_wf)

        # 7. Test GET /runs/{run_id}/events API endpoint returns complete memory snapshots
        detail = (await ac.get(f"{BASE}/runs/{run_id}/events")).json()
        assert detail["run_id"] == run_id
        assert detail["status"] == "COMPLETED"
        assert len(detail["traces"]) >= 8

        # Verify node traces contain input_snapshot and output_snapshot
        first_trace = detail["traces"][0]
        assert "input_snapshot" in first_trace
        assert "output_snapshot" in first_trace
        assert first_trace["status"] == "COMPLETED"
