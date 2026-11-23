"""
tests/test_approval_roundtrip.py
T18 / I16 — Memverifikasi siklus penuh pending approval:
(a) Approve sekali -> tool jalan 1x, reviewed_by tercatat, jawaban final kembali.
(b) Approve dua kali -> 409 Conflict, tool tetap jalan 1x.
(c) Reject -> status REJECTED, tool jalan 0x.
(d) Expired -> 410 Gone, tool jalan 0x.
(e) Dua approval berbeda berjalan independen.
"""

from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

from httpx import ASGITransport, AsyncClient
import pytest
from pydantic import BaseModel
from sqlalchemy import select

from app.core.database import AsyncSessionLocal, init_db
from app.main import app
from app.models.agent_run import PendingApproval
from app.services.deepseek_agent import DeepSeekAgentService
from app.services.fnb_marketing.tools import PublishInstagramTool, register_fnb_tools


class _FakePublishResult(BaseModel):
    published: bool = True
    campaign_id: int = 101


def _install_fake_llm_and_tool(monkeypatch):
    register_fnb_tools()
    exec_calls = []

    async def _fake_execute(self, config, inputs, context):
        cid = (inputs.approved_payload or {}).get("campaign_id", 101)
        exec_calls.append({"campaign_id": cid, "approved": context.extra_meta.get("action_approved")})
        return _FakePublishResult(published=True, campaign_id=cid)

    monkeypatch.setattr(PublishInstagramTool, "execute", _fake_execute)

    class _FakeCompletions:
        async def create(self, **kwargs):
            msgs = kwargs["messages"]
            last = msgs[-1]
            if last.get("role") == "tool":
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content="Campaign berhasil dipublish setelah approval.", tool_calls=None))]
                )
            user_txt = (last.get("content") or "")
            cid = 202 if "202" in user_txt else 101
            tc = SimpleNamespace(
                id=f"call_ig_{cid}",
                function=SimpleNamespace(
                    name="fnb__publish_instagram",
                    arguments=json.dumps({"approved_payload": {"campaign_id": cid}}),
                ),
            )
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[tc]))]
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions()))
    monkeypatch.setattr(
        DeepSeekAgentService,
        "_get_active_providers",
        classmethod(lambda cls: [{"name": "FakeLLM", "client": fake_client, "model": "fake-model"}]),
    )
    return exec_calls


@pytest.mark.asyncio
async def test_approval_once_and_double_approve_conflict(monkeypatch):
    await init_db()
    exec_calls = _install_fake_llm_and_tool(monkeypatch)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res_chat = await ac.post(
            "/api/v1/agent/chat",
            json={"outlet_id": 1, "message": "Publish campaign 101 ke Instagram."},
        )
        assert res_chat.status_code == 202
        action_id = res_chat.json()["action_id"]
        assert len(exec_calls) == 0

        # (a) Approve sekali -> tool jalan 1x
        res_app1 = await ac.post(
            f"/api/v1/agent/approvals/{action_id}/approve",
            json={"reviewed_by": "juragan_budi"},
        )
        assert res_app1.status_code == 200
        body = res_app1.json()
        assert body["status"] == "DONE"
        assert "berhasil dipublish" in body["reply"]
        assert len(exec_calls) == 1
        assert exec_calls[0]["approved"] is True

        # (b) Approve dua kali -> 409, tool tetap 1x
        res_app2 = await ac.post(
            f"/api/v1/agent/approvals/{action_id}/approve",
            json={"reviewed_by": "juragan_budi"},
        )
        assert res_app2.status_code == 409
        assert len(exec_calls) == 1

    async with AsyncSessionLocal() as session:
        rec = (await session.execute(select(PendingApproval).where(PendingApproval.action_id == action_id))).scalar_one()
        assert rec.status == "APPROVED"
        assert rec.reviewed_by == "juragan_budi"


@pytest.mark.asyncio
async def test_reject_approval_never_executes_tool(monkeypatch):
    await init_db()
    exec_calls = _install_fake_llm_and_tool(monkeypatch)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res_chat = await ac.post(
            "/api/v1/agent/chat",
            json={"outlet_id": 1, "message": "Publish campaign 101 ke Instagram."},
        )
        assert res_chat.status_code == 202
        action_id = res_chat.json()["action_id"]

        # (c) Reject -> 0x eksekusi
        res_rej = await ac.post(
            f"/api/v1/agent/approvals/{action_id}/reject",
            json={"reviewed_by": "owner_sari"},
        )
        assert res_rej.status_code == 200
        assert res_rej.json()["status"] == "REJECTED"
        assert len(exec_calls) == 0

    async with AsyncSessionLocal() as session:
        rec = (await session.execute(select(PendingApproval).where(PendingApproval.action_id == action_id))).scalar_one()
        assert rec.status == "REJECTED"
        assert rec.reviewed_by == "owner_sari"


@pytest.mark.asyncio
async def test_expired_approval_returns_410_and_never_executes(monkeypatch):
    await init_db()
    exec_calls = _install_fake_llm_and_tool(monkeypatch)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res_chat = await ac.post(
            "/api/v1/agent/chat",
            json={"outlet_id": 1, "message": "Publish campaign 101 ke Instagram."},
        )
        assert res_chat.status_code == 202
        action_id = res_chat.json()["action_id"]

        # Paksa expires_at lewat di DB
        async with AsyncSessionLocal() as session:
            rec = (await session.execute(select(PendingApproval).where(PendingApproval.action_id == action_id))).scalar_one()
            rec.expires_at = datetime.now(timezone.utc) - timedelta(minutes=5)
            await session.commit()

        # (d) Expired -> 410, tool jalan 0x
        res_exp = await ac.post(f"/api/v1/agent/approvals/{action_id}/approve")
        assert res_exp.status_code == 410
        assert len(exec_calls) == 0


@pytest.mark.asyncio
async def test_two_pending_approvals_run_independently_and_404(monkeypatch):
    await init_db()
    exec_calls = _install_fake_llm_and_tool(monkeypatch)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res_1 = await ac.post(
            "/api/v1/agent/chat",
            json={"outlet_id": 1, "message": "Publish campaign 101 ke Instagram."},
        )
        res_2 = await ac.post(
            "/api/v1/agent/chat",
            json={"outlet_id": 1, "message": "Publish campaign 202 ke Instagram."},
        )
        assert res_1.status_code == 202
        assert res_2.status_code == 202
        action_1 = res_1.json()["action_id"]
        action_2 = res_2.json()["action_id"]
        assert action_1 != action_2

        # (e) Reject action_1, approve action_2 -> hanya campaign 202 yang jalan
        rej_1 = await ac.post(f"/api/v1/agent/approvals/{action_1}/reject")
        assert rej_1.status_code == 200
        assert rej_1.json()["status"] == "REJECTED"

        app_2 = await ac.post(f"/api/v1/agent/approvals/{action_2}/approve")
        assert app_2.status_code == 200
        assert app_2.json()["status"] == "DONE"
        assert exec_calls == [{"campaign_id": 202, "approved": True}]

        # Bonus: action_id tidak dikenal -> 404
        res_404 = await ac.post("/api/v1/agent/approvals/non-existent-id/approve")
        assert res_404.status_code == 404
