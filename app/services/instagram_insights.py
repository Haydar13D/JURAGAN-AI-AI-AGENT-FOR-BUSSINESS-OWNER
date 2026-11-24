"""
app/services/instagram_insights.py
T27 — Feedback loop Instagram Insights (24h & 72h):
- Mengambil reach, saved, profile_visits, follows per post.
- Jika permission insights kurang -> status INSIGHTS_UNAVAILABLE (bukan error).
- Menyimpan ke CampaignPerformance, merangkum untuk percakapan berikutnya (tanpa angka jika NULL),
  dan memprioritaskan produk berkinerja bagus untuk kampanye berikutnya.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.logging import logger
from app.models.campaign import Campaign, CampaignContent, CampaignPerformance, SocialPost
from app.services.instagram_api import InstagramApiError, InstagramErrorCode, InstagramGraphClient

INSIGHTS_METRICS = ("reach", "saved", "profile_visits", "follows")
INSIGHTS_WINDOWS = (24, 72)


def _format_compact_count(value: int) -> str:
    if value >= 1000:
        if value % 1000 == 0:
            return f"{value // 1000}k"
        return f"{value / 1000:.1f}k"
    return str(value)


async def collect_post_insights(
    session: AsyncSession,
    *,
    post_id: str,
    window_hours: int,
    outlet_id: int = 1,
    campaign_id: Optional[int] = None,
    content_id: Optional[str] = None,
    product_name: Optional[str] = None,
    graph_client: Optional[InstagramGraphClient] = None,
) -> CampaignPerformance:
    """Mengambil dan menyimpan metrik Instagram Insights (24h atau 72h) secara idempoten per (post_id, window_hours)."""
    if window_hours not in INSIGHTS_WINDOWS:
        raise ValueError(f"window_hours harus 24 atau 72, bukan {window_hours}.")

    existing = (
        await session.execute(
            select(CampaignPerformance).where(
                CampaignPerformance.post_id == post_id,
                CampaignPerformance.window_hours == window_hours,
            )
        )
    ).scalar_one_or_none()
    if existing is not None and existing.status == "COLLECTED":
        return existing

    client = graph_client or InstagramGraphClient()
    status = "COLLECTED"
    metrics: Dict[str, Optional[int]] = {m: None for m in INSIGHTS_METRICS}

    try:
        fetched = await client.get_media_insights(post_id, metrics=INSIGHTS_METRICS)
        for m in INSIGHTS_METRICS:
            val = fetched.get(m)
            metrics[m] = int(val) if val is not None else None
    except InstagramApiError as exc:
        if exc.code == InstagramErrorCode.INSIGHTS_UNAVAILABLE:
            logger.warning(f"Instagram Insights tidak tersedia untuk post {post_id} ({window_hours}h): {exc.actionable_message}")
            status = "INSIGHTS_UNAVAILABLE"
        else:
            raise

    if existing is not None:
        existing.status = status
        existing.reach = metrics["reach"]
        existing.saved = metrics["saved"]
        existing.profile_visits = metrics["profile_visits"]
        existing.follows = metrics["follows"]
        if product_name and not existing.product_name:
            existing.product_name = product_name
        await session.commit()
        await session.refresh(existing)
        return existing

    row = CampaignPerformance(
        outlet_id=outlet_id,
        campaign_id=campaign_id,
        content_id=content_id,
        post_id=post_id,
        product_name=product_name,
        window_hours=window_hours,
        status=status,
        reach=metrics["reach"],
        saved=metrics["saved"],
        profile_visits=metrics["profile_visits"],
        follows=metrics["follows"],
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def collect_due_post_insights(
    session: AsyncSession,
    *,
    now_dt: Optional[datetime] = None,
    graph_client: Optional[InstagramGraphClient] = None,
) -> List[CampaignPerformance]:
    """Mengumpulkan insights 24h & 72h untuk semua SocialPost yang sudah melewati jendela waktu."""
    current_dt = now_dt or datetime.now(timezone.utc)
    if current_dt.tzinfo is None:
        current_dt = current_dt.replace(tzinfo=timezone.utc)

    posts = list(
        (
            await session.execute(
                select(SocialPost).where(SocialPost.post_id.is_not(None))
            )
        )
        .scalars()
        .all()
    )

    collected: List[CampaignPerformance] = []
    for post in posts:
        if not post.post_id or not post.published_at:
            continue
        pub_dt = post.published_at
        if pub_dt.tzinfo is None:
            pub_dt = pub_dt.replace(tzinfo=timezone.utc)
        elapsed_hours = (current_dt - pub_dt).total_seconds() / 3600.0

        campaign = (
            await session.execute(select(Campaign).where(Campaign.id == post.campaign_id))
        ).scalar_one_or_none()
        content = (
            await session.execute(select(CampaignContent).where(CampaignContent.post_id == post.post_id))
        ).scalar_one_or_none()

        outlet_id = campaign.outlet_id if campaign else (content.outlet_id if content else 1)
        product_name = campaign.product_name if campaign else None
        content_id = content.content_id if content else None

        for window in INSIGHTS_WINDOWS:
            if elapsed_hours < window:
                continue
            existing = (
                await session.execute(
                    select(CampaignPerformance).where(
                        CampaignPerformance.post_id == post.post_id,
                        CampaignPerformance.window_hours == window,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                continue
            perf = await collect_post_insights(
                session,
                post_id=post.post_id,
                window_hours=window,
                outlet_id=outlet_id,
                campaign_id=post.campaign_id,
                content_id=content_id,
                product_name=product_name,
                graph_client=graph_client,
            )
            collected.append(perf)

    return collected


async def format_recent_insights_summary(
    session: AsyncSession,
    outlet_id: int,
    *,
    days: int = 14,
) -> str:
    """Merangkum performa post IG terbaru untuk percakapan berikutnya. Dilarang menampilkan angka jika NULL."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    try:
        rows = list(
            (
                await session.execute(
                    select(CampaignPerformance)
                    .where(
                        CampaignPerformance.outlet_id == outlet_id,
                        CampaignPerformance.status == "COLLECTED",
                        CampaignPerformance.collected_at >= cutoff,
                    )
                    .order_by(CampaignPerformance.window_hours.desc(), CampaignPerformance.collected_at.desc(), CampaignPerformance.id.desc())
                )
            )
            .scalars()
            .all()
        )
    except OperationalError as exc:
        logger.warning(f"Tabel campaign_performances belum siap saat merangkum insights: {exc}")
        return ""
    if not rows:
        return ""

    latest_by_post: Dict[str, CampaignPerformance] = {}
    for row in rows:
        if row.post_id not in latest_by_post:
            latest_by_post[row.post_id] = row

    lines: List[str] = []
    for row in latest_by_post.values():
        parts: List[str] = []
        if row.reach is not None:
            parts.append(f"reach {_format_compact_count(row.reach)}")
        if row.saved is not None:
            parts.append(f"{_format_compact_count(row.saved)} simpan")
        if row.profile_visits is not None:
            parts.append(f"{_format_compact_count(row.profile_visits)} kunjungan profil")
        if row.follows is not None:
            parts.append(f"{_format_compact_count(row.follows)} pengikut")
        if not parts:
            continue
        label = row.product_name or row.post_id
        lines.append(f"- {label} ({row.window_hours}j): {', '.join(parts)}")

    if not lines:
        return ""
    return "Ringkasan performa post IG terbaru:\n" + "\n".join(lines)


def compute_performance_score(row: CampaignPerformance) -> float:
    """Skor performa konten deterministik dari metrik non-NULL."""
    if row.status != "COLLECTED":
        return 0.0
    score = (
        (row.reach or 0) * 0.01
        + (row.saved or 0) * 2.0
        + (row.profile_visits or 0) * 1.0
        + (row.follows or 0) * 3.0
    )
    return round(float(score), 2)


async def get_top_performing_products(
    session: AsyncSession,
    outlet_id: int,
    *,
    days: int = 30,
) -> Dict[str, float]:
    """Mengembalikan skor performa per produk dari CampaignPerformance untuk prioritas kampanye berikutnya."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = list(
        (
            await session.execute(
                select(CampaignPerformance).where(
                    CampaignPerformance.outlet_id == outlet_id,
                    CampaignPerformance.status == "COLLECTED",
                    CampaignPerformance.product_name.is_not(None),
                    CampaignPerformance.collected_at >= cutoff,
                )
            )
        )
        .scalars()
        .all()
    )
    scores: Dict[str, float] = {}
    for row in rows:
        if not row.product_name:
            continue
        s = compute_performance_score(row)
        if s > 0:
            key = row.product_name.strip()
            scores[key] = max(scores.get(key, 0.0), s)
    return scores


def prioritize_opportunities_by_performance(
    opportunities: Sequence[Any],
    performance_scores: Optional[Dict[str, float]],
) -> List[Any]:
    """Mengurutkan kandidat kampanye agar produk dengan performa konten bagus diprioritaskan lebih dulu."""
    items = list(opportunities)
    if not performance_scores:
        return items
    lowered_scores = {k.strip().lower(): float(v) for k, v in performance_scores.items()}

    def _score_for(opp: Any) -> float:
        name = getattr(opp, "product_name", None)
        if name is None and isinstance(opp, dict):
            name = opp.get("product_name")
        if not name:
            return 0.0
        return lowered_scores.get(str(name).strip().lower(), 0.0)

    return sorted(items, key=_score_for, reverse=True)


async def run_insights_collector_job() -> None:
    try:
        async with AsyncSessionLocal() as session:
            await collect_due_post_insights(session)
    except Exception as exc:
        logger.error(f"Error pada Instagram insights collector job: {exc}")


def register_insights_scheduler_job(scheduler) -> None:
    scheduler.add_job(
        run_insights_collector_job,
        trigger=CronTrigger(hour="*/6", minute=10, timezone=ZoneInfo(settings.TIMEZONE)),
        id="instagram_insights_collector",
        replace_existing=True,
    )
