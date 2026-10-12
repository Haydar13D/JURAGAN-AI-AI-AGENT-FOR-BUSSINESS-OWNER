import pytest
from unittest.mock import patch, AsyncMock
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.services.daily_reporter import DailyReporterService

@pytest.mark.asyncio
async def test_daily_reporter_text_generation():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/api/v1/analytics/daily-report-preview?outlet_id=1")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "success"
        assert "LAPORAN PENJUALAN HARIAN" in data["report_text"]
        assert "Total Omzet" in data["report_text"]

@pytest.mark.asyncio
async def test_send_daily_report_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Mock send_telegram_message to prevent spamming actual Telegram during automated test runs
        with patch.object(DailyReporterService, "send_telegram_message", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True
            res = await ac.post("/api/v1/analytics/send-daily-report?outlet_id=1&chat_id=2034469547")
            assert res.status_code == 200
            data = res.json()
            assert data["status"] == "success"
            assert "berhasil dikirim" in data["message"]
            assert data["chat_id"] == 2034469547
            mock_send.assert_awaited_once()

@pytest.mark.asyncio
async def test_telegram_webhook_commands():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Mock send_message on TelegramBotManager
        with patch("app.bot.telegram_bot.TelegramBotManager.send_message", new_callable=AsyncMock) as mock_send:
            # 1. Test /start command
            update_start = {
                "update_id": 1001,
                "message": {
                    "message_id": 1,
                    "chat": {"id": 2034469547, "first_name": "Juragan"},
                    "text": "/start"
                }
            }
            res_start = await ac.post("/api/v1/telegram/webhook", json=update_start)
            assert res_start.status_code == 200
            mock_send.assert_awaited()
            # Verify welcome message content sent to chat_id
            call_args = mock_send.call_args[0]
            assert call_args[0] == 2034469547
            assert "Selamat Datang di JuraganAI" in call_args[1]

            mock_send.reset_mock()

            # 2. Test /omzet command
            update_omzet = {
                "update_id": 1002,
                "message": {
                    "message_id": 2,
                    "chat": {"id": 2034469547},
                    "text": "/omzet"
                }
            }
            res_omzet = await ac.post("/api/v1/telegram/webhook", json=update_omzet)
            assert res_omzet.status_code == 200
            mock_send.assert_awaited()
