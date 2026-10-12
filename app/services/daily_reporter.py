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
    async def run_daily_job(cls):
        logger.info("Memulai eksekusi Daily Scheduled Report Job...")
        async with AsyncSessionLocal() as session:
            report_text = await cls.generate_daily_report_text(session, settings.DEFAULT_OUTLET_ID)
            logger.info(f"Report Harian yang dihasilkan:\n{report_text}")

            if settings.TELEGRAM_DEFAULT_CHAT_ID:
                await cls.send_telegram_message(settings.TELEGRAM_DEFAULT_CHAT_ID, report_text)
            else:
                logger.info("TELEGRAM_DEFAULT_CHAT_ID belum diset. Laporan dicatat di log sistem.")

def init_daily_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.TIMEZONE)
    scheduler.add_job(
        DailyReporterService.run_daily_job,
        trigger="cron",
        hour=settings.DAILY_REPORT_CRON_HOUR,
        minute=settings.DAILY_REPORT_CRON_MINUTE,
        id="daily_sales_report_job",
        replace_existing=True
    )
    return scheduler
