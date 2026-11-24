"""
app/services/posting_scheduler.py
T26 — Penjadwalan tayang Instagram berdasarkan data peak hours kasir (1 jam sebelum jam ramai teratas),
idempoten per (content_id, tanggal), dan hanya memicu publish untuk konten APPROVED.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.logging import logger
from app.models.campaign import CampaignContent, PostingSchedule
from app.services.analytics_engine import SalesAnalyticsEngine
from app.services.instagram_publisher import InstagramPublisherService

DEFAULT_POSTING_SLOT = "11:00"


class PostingRecommendation(BaseModel):
    slot_time: str
    peak_hour: Optional[int] = None
    source: str  # peak_hours | default_slot
    reason: str


async def recommend_posting_slot(
    session: AsyncSession,
    outlet_id: int,
    date_str: str,
) -> PostingRecommendation:
    """Menghitung slot tayang = 1 jam sebelum jam ramai teratas, atau DEFAULT_SLOT jika data kosong."""
    peak_items = await SalesAnalyticsEngine.get_peak_hours(session, outlet_id, date_str)
    valid_items = [item for item in peak_items if item.transaction_count > 0]
    if not valid_items:
        return PostingRecommendation(
            slot_time=DEFAULT_POSTING_SLOT,
            peak_hour=None,
            source="default_slot",
            reason=f"Data peak hours untuk {date_str} kosong; memakai DEFAULT_SLOT {DEFAULT_POSTING_SLOT}.",
        )

    top_item = max(valid_items, key=lambda x: (x.transaction_count, x.total_revenue))
    slot_hour = (top_item.hour - 1) % 24
    slot_time = f"{slot_hour:02d}:00"
    return PostingRecommendation(
        slot_time=slot_time,
        peak_hour=top_item.hour,
        source="peak_hours",
        reason=f"tayang jam {slot_time}, 1 jam sebelum jam ramai ({top_item.hour:02d}:00)",
    )


async def schedule_content_post(
    session: AsyncSession,
    *,
    content_id: str,
    outlet_id: int,
    slot_date: str,
    recommendation: PostingRecommendation,
) -> PostingSchedule:
    """Menyimpan jadwal tayang secara idempoten per (content_id, slot_date)."""
    existing = (
        await session.execute(
            select(PostingSchedule).where(
                PostingSchedule.content_id == content_id,
                PostingSchedule.slot_date == slot_date,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    row = PostingSchedule(
        content_id=content_id,
        outlet_id=outlet_id,
        slot_date=slot_date,
        slot_time=recommendation.slot_time,
        peak_hour=recommendation.peak_hour,
        recommendation_reason=recommendation.reason,
        status="SCHEDULED",
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def run_due_posting_slots(
    session: AsyncSession,
    *,
    now_dt: Optional[datetime] = None,
    sandbox: Optional[bool] = None,
) -> List[Dict[str, Any]]:
    """Memicu publish untuk seluruh jadwal yang sudah jatuh tempo dan kontennya berstatus APPROVED."""
    tz = ZoneInfo(settings.TIMEZONE)
    current_dt = now_dt or datetime.now(tz)
    today_str = current_dt.date().isoformat()
    current_hhmm = current_dt.strftime("%H:%M")

    schedules = list(
        (
            await session.execute(
                select(PostingSchedule).where(
                    PostingSchedule.status == "SCHEDULED",
                    PostingSchedule.slot_date == today_str,
                    PostingSchedule.slot_time <= current_hhmm,
                )
            )
        )
        .scalars()
        .all()
    )

    results: List[Dict[str, Any]] = []
    for sched in schedules:
        content = (
            await session.execute(
                select(CampaignContent).where(CampaignContent.content_id == sched.content_id)
            )
        ).scalar_one_or_none()
        if content is None or content.status != "APPROVED":
            continue

        pub_res = await InstagramPublisherService.publish_content(
            session,
            sched.content_id,
            idempotency_key=f"sched_{sched.content_id}_{sched.slot_date}",
            sandbox=sandbox,
        )
        sched.status = str(pub_res.get("status") or "PUBLISHED")
        sched.post_id = pub_res.get("post_id")
        await session.commit()
        results.append(pub_res)

    return results


async def run_posting_scheduler_job() -> None:
    try:
        async with AsyncSessionLocal() as session:
            await run_due_posting_slots(session)
    except Exception as exc:
        logger.error(f"Error pada posting scheduler job: {exc}")


def register_posting_scheduler_job(scheduler) -> None:
    scheduler.add_job(
        run_posting_scheduler_job,
        trigger=CronTrigger(minute="*/15", timezone=settings.TIMEZONE),
        id="instagram_posting_scheduler",
        replace_existing=True,
    )
