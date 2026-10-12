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
async def test_agent_chat_endpoint():
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
