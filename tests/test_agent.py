import json
from types import SimpleNamespace

import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.services.deepseek_agent import DeepSeekAgentService


@pytest.fixture
def fake_llm_provider(monkeypatch):
    class _FakeCompletions:
        async def create(self, **kwargs):
            msgs = kwargs["messages"]
            last = msgs[-1]
            if last.get("role") == "tool":
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content="Ringkasan data siap.", tool_calls=None))]
                )
            user_txt = (last.get("content") or "").lower()
            if "laku" in user_txt or "laris" in user_txt:
                fn_name = "analytics.get_top_products"
                args = {"start_date": "2026-11-10", "end_date": "2026-11-17"}
            elif "jam" in user_txt or "ramai" in user_txt:
                fn_name = "analytics.get_peak_hours"
                args = {"date": "2026-11-17"}
            else:
                fn_name = "analytics.get_sales_overview"
                args = {"start_date": "2026-11-10", "end_date": "2026-11-17"}

            tc = SimpleNamespace(
                id="call_test_agent",
                function=SimpleNamespace(name=fn_name, arguments=json.dumps(args)),
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


@pytest.mark.asyncio
async def test_agent_single_turn_and_session_creation(fake_llm_provider):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        payload = {
            "outlet_id": 1,
            "message": "Menu apa yang paling laku 7 hari terakhir?"
        }
        res = await ac.post("/api/v1/agent/chat", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert "reply" in data
        assert "conversation_id" in data
        assert data["conversation_id"] is not None
        assert "analytics.get_top_products" in data["tools_executed"]

@pytest.mark.asyncio
async def test_agent_multi_turn_conversation_and_memory(fake_llm_provider):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Turn 1
        payload_1 = {
            "outlet_id": 1,
            "message": "Berapa omzet dan total transaksi minggu ini?"
        }
        res_1 = await ac.post("/api/v1/agent/chat", json=payload_1)
        assert res_1.status_code == 200
        data_1 = res_1.json()
        conv_id = data_1["conversation_id"]
        assert conv_id is not None

        # Turn 2: Follow-up question referencing context
        payload_2 = {
            "outlet_id": 1,
            "conversation_id": conv_id,
            "message": "Bagaimana dengan jam ramainya?"
        }
        res_2 = await ac.post("/api/v1/agent/chat", json=payload_2)
        assert res_2.status_code == 200
        data_2 = res_2.json()
        assert data_2["conversation_id"] == conv_id
        assert "analytics.get_peak_hours" in data_2["tools_executed"]

        # Check history retrieval
        res_hist = await ac.get(f"/api/v1/agent/conversations/{conv_id}?outlet_id=1")
        assert res_hist.status_code == 200
        hist_data = res_hist.json()
        assert hist_data["conversation_id"] == conv_id
        assert hist_data["total_messages"] >= 4
        assert hist_data["messages"][0]["role"] == "user"
        assert hist_data["messages"][1]["role"] == "assistant"

        # Test Reset Conversation
        res_reset = await ac.delete(f"/api/v1/agent/conversations/{conv_id}?outlet_id=1")
        assert res_reset.status_code == 200
        reset_data = res_reset.json()
        assert reset_data["status"] == "success"

        # Confirm conversation is gone
        res_check = await ac.get(f"/api/v1/agent/conversations/{conv_id}?outlet_id=1")
        assert res_check.status_code == 404

@pytest.mark.asyncio
async def test_multi_provider_configuration_and_fallback():
    from app.services.deepseek_agent import DeepSeekAgentService
    from app.core.config import settings

    # Test provider retrieval
    providers = DeepSeekAgentService._get_active_providers()
    assert isinstance(providers, list)
    # Check that settings contain openrouter and atmorouter attributes
    assert hasattr(settings, "LLM_PROVIDER")
    assert hasattr(settings, "ATMOROUTER_API_KEY")
    assert hasattr(settings, "ATMOROUTER_BASE_URL")
    assert hasattr(settings, "ATMOROUTER_MODEL")
    assert hasattr(settings, "OPENROUTER_API_KEY")
    assert hasattr(settings, "OPENROUTER_MODEL")
    assert hasattr(settings, "OPENROUTER_BASE_URL")



