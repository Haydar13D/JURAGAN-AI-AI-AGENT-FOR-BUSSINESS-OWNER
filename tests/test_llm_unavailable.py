"""
tests/test_llm_unavailable.py
T11 — Bunuh fallback yang berpura-pura.
Memverifikasi:
1. _fallback_local_agent dan _rule_based_patch_parser sudah dihapus (hasattr False).
2. Saat provider LLM kosong, POST /api/v1/agent/chat mengembalikan HTTP 503 dengan {"code": "LLM_UNAVAILABLE"}.
3. Saat provider LLM kosong, ProposalGenerator.generate_proposal() mengembalikan is_valid=False dengan error LLM_UNAVAILABLE.
"""

import json
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.schemas.workflow import WorkflowDefinition
from app.services.deepseek_agent import DeepSeekAgentService
from app.services.proposal_generator import ProposalGenerator


def test_legacy_fake_fallbacks_are_removed():
    assert not hasattr(DeepSeekAgentService, "_fallback_local_agent")
    assert not hasattr(ProposalGenerator, "_rule_based_patch_parser")


@pytest.mark.asyncio
async def test_agent_chat_returns_503_when_no_llm_provider(monkeypatch):
    monkeypatch.setattr(
        DeepSeekAgentService,
        "_get_active_providers",
        classmethod(lambda cls: []),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/agent/chat",
            json={"outlet_id": 1, "message": "Berapa omzet hari ini?"},
        )

    assert resp.status_code == 503
    data = resp.json()
    assert data["code"] == "LLM_UNAVAILABLE"


@pytest.mark.asyncio
async def test_proposal_generator_returns_llm_unavailable_when_no_provider(monkeypatch):
    monkeypatch.setattr(
        DeepSeekAgentService,
        "_get_active_providers",
        classmethod(lambda cls: []),
    )
    with open("data/fixtures/fnb_marketing_template.json", "r", encoding="utf-8") as f:
        wf = WorkflowDefinition.model_validate(json.load(f))

    proposal = await ProposalGenerator.generate_proposal(wf, "Ubah diskon jadi 20%")
    assert proposal.is_valid is False
    assert any("LLM_UNAVAILABLE" in err for err in proposal.validation_errors)
