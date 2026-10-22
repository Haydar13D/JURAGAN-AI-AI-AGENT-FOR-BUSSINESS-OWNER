"""
tests/test_kirata_api.py
End-to-end API tests for the Kirata template-first journey (PRD v2.0 §5, §14).
"""

import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app

BASE = "/api/v1/workflows"


@pytest.mark.asyncio
async def test_template_gallery_and_tool_catalog():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        tpls = (await ac.get(f"{BASE}/templates")).json()
        ids = {t["id"]: t for t in tpls}
        iphone = ids["tpl_used_iphone_pricing_v1"]
        assert iphone["status"] == "AVAILABLE"
        for key in ("business_outcome", "required_inputs", "expected_output", "limitations", "tools", "onboarding_fields"):
            assert iphone[key]
        assert iphone["node_count"] == 9
        assert ids["tpl_fnb_sales_to_marketing_v1"]["status"] == "AVAILABLE"
        assert ids["tpl_fashion_cataloging"]["status"] == "ROADMAP"

        tools = (await ac.get(f"{BASE}/tools")).json()
        types = {t["tool_type"]: t for t in tools}
        assert types["governance.action_approval"]["requires_action_approval"] is True
        assert types["retail.export_catalog"]["is_side_effect"] is True
        assert "target_gross_margin" in types["retail.calculate_pricing"]["config_schema"]["properties"]


@pytest.mark.asyncio
async def test_locked_template_cannot_be_instantiated():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.post(f"{BASE}/from-template", json={"template_id": "tpl_fashion_cataloging"})
        assert r.status_code == 400



@pytest.mark.asyncio
async def test_full_template_first_journey():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Onboarding applies values; v1 is DRAFT
        r = await ac.post(f"{BASE}/from-template", json={
            "template_id": "tpl_used_iphone_pricing_v1",
            "name": "Toko Test",
            "onboarding": {"target_gross_margin_pct": 12, "overhead_fee": 40000}
        })
        assert r.status_code == 200
        wf = r.json()["workflow"]
        wf_id = wf["id"]
        assert wf["status"] == "DRAFT"
        calc = next(n for n in wf["nodes"] if n["id"] == "calculate_pricing")
        assert calc["config"]["target_gross_margin"] == 0.12
        assert calc["config"]["overhead_fee"] == 40000

        # Draft cannot run (FR-06)
        assert (await ac.post(f"{BASE}/{wf_id}/runs", json={})).status_code == 400

        # Dry-run + approve v1
        dry = (await ac.post(f"{BASE}/{wf_id}/dry-run?version=1")).json()
        assert dry["is_clean"] is True
        assert (await ac.post(f"{BASE}/{wf_id}/approve?version=1")).status_code == 200

        # Reconfigure → draft v2, active stays v1 (FR-14)
        prop = (await ac.post(f"{BASE}/{wf_id}/proposals", json={"instruction": "margin 15%"})).json()
        assert prop["is_valid"] is True
        summary = (await ac.get(f"{BASE}/{wf_id}")).json()
        assert summary["active_version"] == 1
        assert summary["latest_version"] == 2

        # Reject draft keeps v1 active
        rej = (await ac.post(f"{BASE}/{wf_id}/versions/2/reject")).json()
        assert rej["active_version"] == 1

        # Run pauses at action approval; nothing exported
        run = (await ac.post(f"{BASE}/{wf_id}/runs", json={})).json()
        assert run["status"] == "WAITING_APPROVAL"
        assert run["output"] is None
        action_id = run["pending_action"]["action_id"]

        dec = (await ac.post(f"{BASE}/runs/{run['run_id']}/actions/{action_id}/decision",
                             json={"decision": "APPROVE", "reviewer_user_id": "owner"})).json()
        assert dec["status"] == "APPROVED_AND_COMPLETED"
        assert dec["result"]["exported_rows_count"] > 0

        runs = (await ac.get(f"{BASE}/runs")).json()
        assert any(r["run_id"] == run["run_id"] and r["status"] == "COMPLETED" for r in runs)
