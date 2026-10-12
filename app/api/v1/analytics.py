from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.api.deps import get_db
from app.schemas.analytics import (
    SalesOverview,
    ProductPerformanceItem,
    PeakHourItem,
    PaymentMethodItem,
    PeriodComparison,
    DailySalesTrendItem,
    CategoryContributionItem,
    DayOfWeekAnalysis,
    BasketSizeItem
)
from app.services.analytics_engine import SalesAnalyticsEngine
from app.services.daily_reporter import DailyReporterService

router = APIRouter(prefix="/analytics", tags=["Sales Analytics Engine"])

@router.get("/overview", response_model=SalesOverview, summary="Ringkasan Penjualan Finansial")
async def get_overview(
    outlet_id: int = Query(1, description="ID Outlet"),
    start_date: str = Query(..., description="Format YYYY-MM-DD"),
    end_date: str = Query(..., description="Format YYYY-MM-DD"),
    db: AsyncSession = Depends(get_db)
):
    try:
        return await SalesAnalyticsEngine.get_sales_overview(db, outlet_id, start_date, end_date)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/top-products", response_model=List[ProductPerformanceItem], summary="Daftar Menu Terlaris")
async def get_top_products(
    outlet_id: int = Query(1, description="ID Outlet"),
    start_date: str = Query(..., description="Format YYYY-MM-DD"),
    end_date: str = Query(..., description="Format YYYY-MM-DD"),
    limit: int = Query(5, ge=1, le=50, description="Batas item"),
    sort_by: str = Query("qty", pattern="^(qty|revenue)$", description="qty atau revenue"),
    db: AsyncSession = Depends(get_db)
):
    try:
        return await SalesAnalyticsEngine.get_top_products(db, outlet_id, start_date, end_date, limit, sort_by)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/slow-moving", response_model=List[ProductPerformanceItem], summary="Daftar Menu Paling Sepi (Deadstock)")
async def get_slow_moving(
    outlet_id: int = Query(1, description="ID Outlet"),
    start_date: str = Query(..., description="Format YYYY-MM-DD"),
    end_date: str = Query(..., description="Format YYYY-MM-DD"),
    limit: int = Query(5, ge=1, le=50),
    db: AsyncSession = Depends(get_db)
):
    try:
        return await SalesAnalyticsEngine.get_slow_moving_products(db, outlet_id, start_date, end_date, limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/peak-hours", response_model=List[PeakHourItem], summary="Distribusi Jam Ramai (Peak Hours)")
async def get_peak_hours(
    outlet_id: int = Query(1, description="ID Outlet"),
    date: str = Query(default_factory=lambda: datetime.now().strftime("%Y-%m-%d")),
    db: AsyncSession = Depends(get_db)
):
    try:
        return await SalesAnalyticsEngine.get_peak_hours(db, outlet_id, date)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/compare", response_model=PeriodComparison, summary="Komparasi Dua Periode Penjualan")
async def compare_periods(
    outlet_id: int = Query(1, description="ID Outlet"),
    period_a_start: str = Query(..., description="Tanggal mulai periode A (terbaru)"),
    period_a_end: str = Query(..., description="Tanggal akhir periode A (terbaru)"),
    period_b_start: str = Query(..., description="Tanggal mulai periode B (pembanding)"),
    period_b_end: str = Query(..., description="Tanggal akhir periode B (pembanding)"),
    db: AsyncSession = Depends(get_db)
):
    try:
        return await SalesAnalyticsEngine.compare_sales_period(
            db, outlet_id, period_a_start, period_a_end, period_b_start, period_b_end
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/payment-methods", response_model=List[PaymentMethodItem], summary="Statistik Metode Pembayaran")
async def get_payment_methods(
    outlet_id: int = Query(1, description="ID Outlet"),
    start_date: str = Query(..., description="Format YYYY-MM-DD"),
    end_date: str = Query(..., description="Format YYYY-MM-DD"),
    db: AsyncSession = Depends(get_db)
):
    try:
        return await SalesAnalyticsEngine.get_payment_method_stats(db, outlet_id, start_date, end_date)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/daily-report-preview", summary="Pratinjau Pesan Laporan Harian Otomatis")
async def get_daily_report_preview(
    outlet_id: int = Query(1, description="ID Outlet"),
    target_date: Optional[str] = Query(None, description="Format YYYY-MM-DD (default: hari ini)"),
    db: AsyncSession = Depends(get_db)
):
    try:
        text = await DailyReporterService.generate_daily_report_text(db, outlet_id, target_date)
        return {
            "status": "success",
            "date": target_date or datetime.now().strftime("%Y-%m-%d"),
            "outlet_id": outlet_id,
            "report_text": text
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/send-daily-report", summary="Kirim Laporan Harian ke Telegram Sekarang")
async def send_daily_report_now(
    outlet_id: int = Query(1, description="ID Outlet"),
    chat_id: Optional[int] = Query(None, description="Chat ID Telegram (opsional, default dari .env)"),
    db: AsyncSession = Depends(get_db)
):
    target_chat_id = chat_id or settings.TELEGRAM_DEFAULT_CHAT_ID
    if not target_chat_id:
        raise HTTPException(
            status_code=400,
            detail="Chat ID tidak disediakan dan TELEGRAM_DEFAULT_CHAT_ID belum dikonfigurasi di .env"
        )
    report_text = await DailyReporterService.generate_daily_report_text(db, outlet_id=outlet_id)
    sent = await DailyReporterService.send_telegram_message(target_chat_id, report_text)
    if not sent:
        raise HTTPException(
            status_code=500,
            detail="Gagal mengirim laporan ke Telegram. Periksa TELEGRAM_BOT_TOKEN atau koneksi internet."
        )
    return {
        "status": "success",
        "message": f"Laporan harian berhasil dikirim ke Telegram chat {target_chat_id}",
        "chat_id": target_chat_id,
        "report_preview": report_text
    }

@router.get("/daily-trend", response_model=List[DailySalesTrendItem], summary="Tren Penjualan Harian")
async def get_daily_trend(
    outlet_id: int = Query(1, description="ID Outlet"),
    start_date: str = Query(..., description="Format YYYY-MM-DD"),
    end_date: str = Query(..., description="Format YYYY-MM-DD"),
    db: AsyncSession = Depends(get_db)
):
    try:
        return await SalesAnalyticsEngine.get_daily_sales_trend(db, outlet_id, start_date, end_date)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/category-breakdown", response_model=List[CategoryContributionItem], summary="Kontribusi Omzet per Kategori")
async def get_category_breakdown(
    outlet_id: int = Query(1, description="ID Outlet"),
    start_date: str = Query(..., description="Format YYYY-MM-DD"),
    end_date: str = Query(..., description="Format YYYY-MM-DD"),
    db: AsyncSession = Depends(get_db)
):
    try:
        return await SalesAnalyticsEngine.get_category_contribution(db, outlet_id, start_date, end_date)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/day-of-week", response_model=DayOfWeekAnalysis, summary="Analisis Performa Hari Kerja vs Akhir Pekan")
async def get_day_of_week(
    outlet_id: int = Query(1, description="ID Outlet"),
    start_date: str = Query(..., description="Format YYYY-MM-DD"),
    end_date: str = Query(..., description="Format YYYY-MM-DD"),
    db: AsyncSession = Depends(get_db)
):
    try:
        return await SalesAnalyticsEngine.get_day_of_week_analysis(db, outlet_id, start_date, end_date)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/basket-size", response_model=List[BasketSizeItem], summary="Distribusi Basket Size / Jumlah Menu per Struk")
async def get_basket_size(
    outlet_id: int = Query(1, description="ID Outlet"),
    start_date: str = Query(..., description="Format YYYY-MM-DD"),
    end_date: str = Query(..., description="Format YYYY-MM-DD"),
    db: AsyncSession = Depends(get_db)
):
    try:
        return await SalesAnalyticsEngine.get_basket_size_distribution(db, outlet_id, start_date, end_date)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
