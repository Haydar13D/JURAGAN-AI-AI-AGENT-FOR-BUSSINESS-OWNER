import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app

@pytest.mark.asyncio
async def test_health_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["database"] == "connected"

@pytest.mark.asyncio
async def test_analytics_overview_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get(
            "/api/v1/analytics/overview",
            params={"outlet_id": 1, "start_date": "2026-10-01", "end_date": "2026-10-12"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "total_gmv" in data
        assert "total_transactions" in data
        assert "average_order_value" in data

@pytest.mark.asyncio
async def test_agent_chat_endpoint(monkeypatch):
    import json
    from types import SimpleNamespace
    from app.services.deepseek_agent import DeepSeekAgentService

    class _FakeCompletions:
        async def create(self, **kwargs):
            last = kwargs["messages"][-1]
            if last.get("role") == "tool":
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content="Omzet minggu ini siap.", tool_calls=None))]
                )
            tc = SimpleNamespace(
                id="call_api_test",
                function=SimpleNamespace(
                    name="analytics.get_sales_overview",
                    arguments=json.dumps({"start_date": "2026-11-10", "end_date": "2026-11-17"}),
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

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        payload = {
            "outlet_id": 1,
            "message": "Berapa omzet minggu ini dan menu apa yang paling laku?"
        }
        response = await ac.post("/api/v1/agent/chat", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert "reply" in data
        assert len(data["tools_executed"]) > 0

@pytest.mark.asyncio
async def test_daily_report_preview_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/api/v1/analytics/daily-report-preview", params={"outlet_id": 1})
        assert response.status_code == 200
        data = response.json()
        assert "report_text" in data
        assert "LAPORAN PENJUALAN HARIAN" in data["report_text"]


@pytest.mark.asyncio
async def test_agent_chat_returns_202_and_action_id_on_approval_required(monkeypatch):
    """T14: Tool EXTERNAL (publish_instagram) memicu 202 + action_id tanpa eksekusi."""
    import json
    from types import SimpleNamespace
    from app.services.instagram_publisher import register_instagram_tool
    from app.services.deepseek_agent import DeepSeekAgentService

    register_instagram_tool()

    class _ApprovalFakeCompletions:
        async def create(self, **kwargs):
            tc = SimpleNamespace(
                id="call_ext_ig_001",
                function=SimpleNamespace(
                    name="instagram__publish",
                    arguments=json.dumps({"content_id": "cnt_101", "idempotency_key": "idem_api_101"}),
                ),
            )
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[tc]))]
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=_ApprovalFakeCompletions()))
    monkeypatch.setattr(
        DeepSeekAgentService,
        "_get_active_providers",
        classmethod(lambda cls: [{"name": "FakeLLM", "client": fake_client, "model": "fake-model"}]),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post(
            "/api/v1/agent/chat",
            json={"outlet_id": 1, "message": "Publish campaign 101 ke Instagram sekarang."},
        )
        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "APPROVAL_REQUIRED"
        assert data["action_id"]
        assert data["pending_approval"]["tool_name"] == "instagram__publish"
