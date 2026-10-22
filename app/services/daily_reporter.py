from datetime import datetime, date, timedelta
from typing import Optional
import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import logger
from app.core.database import AsyncSessionLocal
from app.services.analytics_engine import SalesAnalyticsEngine

class DailyReporterService:

    @staticmethod
    async def generate_daily_report_text(session: AsyncSession, outlet_id: int = 1, target_date: Optional[str] = None) -> str:
        if not target_date:
            target_date = datetime.now().strftime("%Y-%m-%d")

        # 1. Metrik Utama
        overview = await SalesAnalyticsEngine.get_sales_overview(session, outlet_id, target_date, target_date)
        top_items = await SalesAnalyticsEngine.get_top_products(session, outlet_id, target_date, target_date, limit=3, sort_by="qty")
        peak_hours = await SalesAnalyticsEngine.get_peak_hours(session, outlet_id, target_date)

        active_peaks = [p for p in peak_hours if p.transaction_count > 0][:2]

        report_lines = [
            f"🌙 *LAPORAN PENJUALAN HARIAN JURAGAN*",
            f"📅 Tanggal: *{target_date}*",
            f"🏪 Outlet: *Outlet Juragan #{outlet_id}*",
            f"━━━━━━━━━━━━━━━━━━━━━━",
            f"💰 *Total Omzet*: Rp{overview.total_gmv:,.0f}",
            f"🧾 *Total Transaksi*: {overview.total_transactions} nota",
            f"🏷️ *Rata-rata Nota (AOV)*: Rp{overview.average_order_value:,.0f}",
            f"📦 *Produk Terjual*: {overview.total_items_sold} pcs",
            f"━━━━━━━━━━━━━━━━━━━━━━",
            f"🏆 *3 Menu Terlaris Hari Ini*:"
        ]

        if top_items:
            for idx, it in enumerate(top_items, 1):
                report_lines.append(f"{idx}. {it.product_name} — *{it.quantity_sold} pcs* (Rp{it.total_revenue:,.0f})")
        else:
            report_lines.append("• Belum ada transaksi menu hari ini.")

        report_lines.append(f"━━━━━━━━━━━━━━━━━━━━━━")
        if active_peaks:
            peak_str = ", ".join([f"{p.hour_label} ({p.transaction_count} tx)" for p in active_peaks])
            report_lines.append(f"⏰ *Jam Tersibuk*: {peak_str}")

        # Insight JuraganAI
        report_lines.append(f"💡 *Insight JuraganAI*:")
        if overview.total_transactions > 0 and top_items:
            top_name = top_items[0].product_name
            report_lines.append(f"Performa hari ini mantap! Penjualan didorong kuat oleh '{top_name}'. Untuk esok hari, pastikan bahan baku aman sejak pagi ya Juragan!")
        else:
            report_lines.append("Hari ini belum ada transaksi tercatat. Pastikan seluruh transaksi kasir POS sudah di-export dan diimpor ke sistem.")

        return "\n".join(report_lines)

    @staticmethod
    async def send_telegram_message(chat_id: int, text: str) -> bool:
        if not settings.TELEGRAM_BOT_TOKEN:
            logger.warning("TELEGRAM_BOT_TOKEN belum diset. Pengiriman pesan Telegram dibatalkan.")
            return False

        url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown"
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(url, json=payload)
                if res.status_code == 200:
                    logger.info(f"Laporan Telegram sukses terkirim ke chat_id {chat_id}")
                    return True
                else:
                    # Fallback plain text jika formatting Markdown bentrok
                    payload.pop("parse_mode", None)
                    res2 = await client.post(url, json=payload)
                    if res2.status_code == 200:
                        logger.info(f"Laporan Telegram sukses terkirim (mode plain text) ke chat_id {chat_id}")
                        return True
                    logger.error(f"Gagal kirim pesan Telegram: {res.text}")
                    return False
        except Exception as e:
            logger.error(f"Eksepsi saat mengirim pesan Telegram: {str(e)}")
            return False

    @classmethod
    async def run_morning_briefing(cls):
        """Job Pukul 08:00 WIB: Laporan operasional pagi."""
        logger.info("[Scheduler 08:00 WIB] Memulai eksekusi Morning Briefing...")
        async with AsyncSessionLocal() as session:
            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            report_text = f"☀️ *BRIEFING PAGI JURAGAN (08:00 WIB)*\n\n"
            report_text += await cls.generate_daily_report_text(session, settings.DEFAULT_OUTLET_ID, yesterday)
            if settings.TELEGRAM_DEFAULT_CHAT_ID:
                await cls.send_telegram_message(settings.TELEGRAM_DEFAULT_CHAT_ID, report_text)

    @classmethod
    async def run_midmorning_update(cls):
        """Job Pukul 10:00 WIB: Update penjualan awal shift siang."""
        logger.info("[Scheduler 10:00 WIB] Memulai eksekusi Mid-Morning Update...")
        async with AsyncSessionLocal() as session:
            today = datetime.now().strftime("%Y-%m-%d")
            overview = await SalesAnalyticsEngine.get_sales_overview(session, settings.DEFAULT_OUTLET_ID, today, today)
            msg = (
                f"☕ *UPDATE PENJUALAN SIANG (10:00 WIB)*\n"
                f"Outlet: *Outlet Juragan #{settings.DEFAULT_OUTLET_ID}*\n"
                f"• Transaksi Masuk: *{overview.total_transactions} nota*\n"
                f"• Omzet Terkumpul: *Rp{overview.total_gmv:,.0f}*\n"
                f"Semangat menyambut jam makan siang Juragan! 🚀"
            )
            if settings.TELEGRAM_DEFAULT_CHAT_ID:
                await cls.send_telegram_message(settings.TELEGRAM_DEFAULT_CHAT_ID, msg)

    @classmethod
    async def run_nightly_campaign_dispatch(cls, outlet_id: int = 2):
        """Job Pukul 22:00 WIB: Scan transaksi harian dan kirim proposal kampanye jika ada anomali."""
        logger.info("[Scheduler 22:00 WIB] Memulai eksekusi Nightly Opportunity Scan & Campaign Dispatch...")
        from app.services.opportunity_detector import OpportunityDetector
        from app.services.campaign_planner import CampaignPlannerService
        from app.bot.telegram_bot import TelegramBotManager

        async with AsyncSessionLocal() as session:
            candidates = await OpportunityDetector.detect_opportunities(session, outlet_id=outlet_id, cooldown_days=7)
            if not candidates:
                logger.info("[Scheduler 22:00 WIB] Semua menu stabil, tidak ada proposal baru malam ini.")
                return None

            top_cand = candidates[0]
            opp = await OpportunityDetector.create_opportunity_record(session, top_cand)
            campaign = await CampaignPlannerService.plan_campaign_for_opportunity(session, opp.id)

            if campaign and settings.TELEGRAM_DEFAULT_CHAT_ID:
                ev = top_cand.get("evidence", {})
                await TelegramBotManager.send_campaign_proposal(
                    chat_id=settings.TELEGRAM_DEFAULT_CHAT_ID,
                    campaign=campaign,
                    evidence=ev
                )
                logger.info(f"[Scheduler 22:00 WIB] Proposal Campaign #{campaign.id} sukses dikirim ke Telegram owner!")
                return campaign
        return None

def init_daily_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.TIMEZONE)

    # 1. Pukul 08:00 WIB: Morning Operational Briefing
    scheduler.add_job(
        DailyReporterService.run_morning_briefing,
        trigger="cron",
        hour=8,
        minute=0,
        id="morning_briefing_job",
        replace_existing=True
    )

    # 2. Pukul 10:00 WIB: Mid-Morning Sales Update
    scheduler.add_job(
        DailyReporterService.run_midmorning_update,
        trigger="cron",
        hour=10,
        minute=0,
        id="midmorning_update_job",
        replace_existing=True
    )

    # 3. Pukul 22:00 WIB: Nightly Opportunity Detection & Campaign Proposal Dispatch
    scheduler.add_job(
        DailyReporterService.run_nightly_campaign_dispatch,
        trigger="cron",
        hour=22,
        minute=0,
        id="nightly_campaign_proposal_job",
        replace_existing=True
    )

    logger.info("Scheduler terkonfigurasi: 08:00 (Briefing), 10:00 (Update), 22:00 (Proposal Kampanye).")
    return scheduler

