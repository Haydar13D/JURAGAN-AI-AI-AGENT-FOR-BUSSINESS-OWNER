import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app

@pytest.mark.asyncio
async def test_agent_single_turn_and_session_creation():
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
        assert "get_top_products" in data["tools_executed"]

@pytest.mark.asyncio
async def test_agent_multi_turn_conversation_and_memory():
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
        assert "get_peak_hours" in data_2["tools_executed"]

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
    # Check that settings contain openrouter attributes
    assert hasattr(settings, "LLM_PROVIDER")
    assert hasattr(settings, "OPENROUTER_API_KEY")
    assert hasattr(settings, "OPENROUTER_MODEL")
    assert hasattr(settings, "OPENROUTER_BASE_URL")


