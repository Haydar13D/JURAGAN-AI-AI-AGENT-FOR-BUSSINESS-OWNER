"""
tests/test_posting_scheduler.py
T26 — Menguji penjadwalan tayang berbasis peak hours:
- Peak 14:00 -> rekomendasi slot 13:00 ("tayang jam 13:00, 1 jam sebelum jam ramai")
- Data peak kosong -> DEFAULT_SLOT (11:00) dengan alasan jelas
- Konten non-APPROVED tidak dipublish
- Dua kali menjalankan slot sama pada (content_id, tanggal) -> tetap 1 publish
"""

from datetime import datetime
import json
import uuid
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete

from app.core.database import AsyncSessionLocal, init_db
from app.models.campaign import CampaignContent, PostingSchedule
from app.schemas.analytics import PeakHourItem
from app.services.analytics_engine import SalesAnalyticsEngine
from app.services.instagram_publisher import InstagramPublisherService
from app.services.posting_scheduler import (
    DEFAULT_POSTING_SLOT,
    recommend_posting_slot,
    run_due_posting_slots,
    schedule_content_post,
)


@pytest.mark.asyncio
async def test_recommend_slot_one_hour_before_peak(monkeypatch):
    await init_db()

    async def _fake_peak_hours(session, outlet_id, date_str):
        return [
            PeakHourItem(hour=10, hour_label="10:00", transaction_count=12, total_revenue=300000.0),
            PeakHourItem(hour=14, hour_label="14:00", transaction_count=45, total_revenue=1400000.0),
        ]

    monkeypatch.setattr(SalesAnalyticsEngine, "get_peak_hours", _fake_peak_hours)

    async with AsyncSessionLocal() as session:
        rec = await recommend_posting_slot(session, outlet_id=1, date_str="2026-11-24")
        assert rec.slot_time == "13:00"
        assert rec.peak_hour == 14
        assert rec.source == "peak_hours"
        assert "tayang jam 13:00" in rec.reason


@pytest.mark.asyncio
async def test_recommend_slot_falls_back_to_default_when_peak_empty(monkeypatch):
    await init_db()

    async def _empty_peak(session, outlet_id, date_str):
        return []

    monkeypatch.setattr(SalesAnalyticsEngine, "get_peak_hours", _empty_peak)

    async with AsyncSessionLocal() as session:
        rec = await recommend_posting_slot(session, outlet_id=1, date_str="2026-11-24")
        assert rec.slot_time == DEFAULT_POSTING_SLOT
        assert rec.peak_hour is None
        assert rec.source == "default_slot"
        assert "DEFAULT_SLOT" in rec.reason


@pytest.mark.asyncio
async def test_due_posting_slot_idempotent_and_requires_approved(monkeypatch):
    await init_db()
    cid_draft = f"cnt_t26_draft_{uuid.uuid4().hex[:6]}"
    cid_appr = f"cnt_t26_appr_{uuid.uuid4().hex[:6]}"
    slot_date = "2026-11-24"

    publish_calls = []
    orig_publish = InstagramPublisherService.publish_content

    async def _spy_publish(session, content_id, idempotency_key, **kwargs):
        publish_calls.append(content_id)
        return await orig_publish(session, content_id, idempotency_key, **kwargs)

    monkeypatch.setattr(InstagramPublisherService, "publish_content", _spy_publish)

    async with AsyncSessionLocal() as session:
        await session.execute(delete(PostingSchedule).where(PostingSchedule.slot_date == slot_date))
        await session.execute(delete(CampaignContent).where(CampaignContent.campaign_id == 9301))
        for cid, st in [(cid_draft, "DRAFT"), (cid_appr, "APPROVED")]:
            session.add(
                CampaignContent(
                    content_id=cid,
                    campaign_id=9301,
                    outlet_id=1,
                    variant_index=1,
                    style="hard_sell",
                    caption="Kopi Susu Rp25.000",
                    hashtags_json=json.dumps(["#kopi", "#promo", "#umkm"]),
                    cta="Pesan!",
                    image_prompt="Coffee",
                    status=st,
                )
            )
        await session.commit()

        rec = await recommend_posting_slot(session, outlet_id=1, date_str=slot_date)
        rec.slot_time = "13:00"
        await schedule_content_post(
            session, content_id=cid_draft, outlet_id=1, slot_date=slot_date, recommendation=rec
        )
        await schedule_content_post(
            session, content_id=cid_appr, outlet_id=1, slot_date=slot_date, recommendation=rec
        )

        due_now = datetime(2026, 11, 24, 13, 5, tzinfo=ZoneInfo("Asia/Jakarta"))
        res1 = await run_due_posting_slots(session, now_dt=due_now, sandbox=True)
        assert len(res1) == 1
        assert publish_calls == [cid_appr]

        # Jalankan kedua kali pada slot sama -> tetap 1 publish
        res2 = await run_due_posting_slots(session, now_dt=due_now, sandbox=True)
        assert len(res2) == 0
        assert publish_calls == [cid_appr]
