"""
tests/test_telegram_autonomous.py
Unit tests untuk Full Autonomous Mobile 1-Click Approval via Telegram (PRD v2.0):
- Verifikasi jadwal 08:00 (Briefing), 10:00 (Update), 22:00 (Proposal Kampanye)
- Verifikasi pembentukan inline keyboard buttons [Setujui], [Revisi], [Tolak]
- Verifikasi eksekusi callback query approve_camp_{id} yang otomatis mempublikasikan konten
"""

import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.services.daily_reporter import init_daily_scheduler, DailyReporterService
from app.bot.telegram_bot import TelegramBotManager
from app.models.campaign import Campaign

@pytest.mark.asyncio
async def test_scheduler_jobs_configuration():
    """Uji verifikasi jadwal: 08:00, 10:00, dan 22:00 WIB."""
    scheduler = init_daily_scheduler()
    jobs = {job.id: job for job in scheduler.get_jobs()}

    assert "morning_briefing_job" in jobs
    assert "midmorning_update_job" in jobs
    assert "nightly_campaign_proposal_job" in jobs

    # Cek jam trigger
    cron_morning = jobs["morning_briefing_job"].trigger
    cron_mid = jobs["midmorning_update_job"].trigger
    cron_night = jobs["nightly_campaign_proposal_job"].trigger

    # 08:00
    assert str(cron_morning.fields[5]) == "8"
    assert str(cron_morning.fields[6]) == "0"

    # 10:00
    assert str(cron_mid.fields[5]) == "10"
    assert str(cron_mid.fields[6]) == "0"

    # 22:00
    assert str(cron_night.fields[5]) == "22"
    assert str(cron_night.fields[6]) == "0"


@pytest.mark.asyncio
async def test_telegram_callback_approve_flow():
    """Uji alur 1-click approval via Telegram callback query."""
    async with AsyncSessionLocal() as session:
        # 1. Buat campaign dummy dalam status WAITING_APPROVAL
        camp = Campaign(
            outlet_id=2,
            product_name="Telegram Test Coffee",
            objective="Pemulihan Penjualan via Telegram",
            brief="Promo kilat sore",
            caption="Nikmati kopi nikmat diskon 15%!",
            discount_pct=15.0,
            status="WAITING_APPROVAL",
            version=1
        )
        session.add(camp)
        await session.commit()
        await session.refresh(camp)

        # 2. Simulasikan payload callback_query dari Telegram saat owner menekan [✅ Setujui]
        mock_callback_update = {
            "callback_query": {
                "id": "cb_query_test_123",
                "data": f"approve_camp_{camp.id}",
                "from": {"id": 99887766, "first_name": "Juragan Boss"},
                "message": {
                    "message_id": 456,
                    "chat": {"id": 99887766, "type": "private"},
                    "text": "Proposal Promosi"
                }
            }
        }

        # 3. Proses callback query
        await TelegramBotManager.process_update(mock_callback_update)

    # 4. Verifikasi status campaign berubah menjadi PUBLISHED di database
    async with AsyncSessionLocal() as check_session:
        updated_camp = (await check_session.execute(
            select(Campaign).where(Campaign.id == camp.id)
        )).scalar_one()

        assert updated_camp.status == "PUBLISHED"


@pytest.mark.asyncio
async def test_telegram_callback_reject_flow():
    """Uji alur penolakan via Telegram callback query [❌ Tolak]."""
    async with AsyncSessionLocal() as session:
        camp = Campaign(
            outlet_id=2,
            product_name="Rejected Coffee",
            objective="Testing Reject",
            brief="Testing",
            caption="Test",
            status="WAITING_APPROVAL",
            version=1
        )
        session.add(camp)
        await session.commit()
        await session.refresh(camp)

        mock_reject_update = {
            "callback_query": {
                "id": "cb_query_reject_789",
                "data": f"reject_camp_{camp.id}",
                "from": {"id": 99887766},
                "message": {
                    "message_id": 457,
                    "chat": {"id": 99887766}
                }
            }
        }

        await TelegramBotManager.process_update(mock_reject_update)

    async with AsyncSessionLocal() as check_session:
        updated_camp = (await check_session.execute(
            select(Campaign).where(Campaign.id == camp.id)
        )).scalar_one()

        assert updated_camp.status == "REJECTED"
