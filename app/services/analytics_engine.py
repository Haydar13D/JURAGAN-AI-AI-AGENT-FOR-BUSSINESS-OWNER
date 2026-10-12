from datetime import datetime, date, time
from typing import List, Optional, Dict, Any
from sqlalchemy import select, func, and_, extract, desc, asc
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.models.transaction import Transaction, TransactionItem
from app.models.product import Product, Category
from app.schemas.analytics import (
    DateRange,
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

def parse_date_boundary(date_str: str, is_end: bool = False) -> datetime:
    if isinstance(date_str, datetime):
        return date_str
    if isinstance(date_str, date):
        t = time.max if is_end else time.min
        return datetime.combine(date_str, t)
    
    clean_str = str(date_str).strip()
    # Jika hanya format YYYY-MM-DD
    if len(clean_str) == 10:
        d = datetime.strptime(clean_str, "%Y-%m-%d").date()
        t = time(23, 59, 59) if is_end else time(0, 0, 0)
        return datetime.combine(d, t)
    
    # Coba parsing full datetime
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"]:
        try:
            return datetime.strptime(clean_str, fmt)
        except ValueError:
            continue
    d = datetime.strptime(clean_str[:10], "%Y-%m-%d").date()
    t = time(23, 59, 59) if is_end else time(0, 0, 0)
    return datetime.combine(d, t)

class SalesAnalyticsEngine:

    @staticmethod
    async def get_sales_overview(
        session: AsyncSession,
        outlet_id: int,
        start_date: str,
        end_date: str
    ) -> SalesOverview:
        dt_start = parse_date_boundary(start_date, is_end=False)
        dt_end = parse_date_boundary(end_date, is_end=True)

        # 1. Agregasi Transaksi
        tx_stmt = select(
            func.coalesce(func.sum(Transaction.total_amount), 0).label("gmv"),
            func.coalesce(func.sum(Transaction.discount), 0).label("discounts"),
            func.count(Transaction.id).label("total_tx")
        ).where(
            Transaction.outlet_id == outlet_id,
            Transaction.status == "COMPLETED",
            Transaction.transaction_time >= dt_start,
            Transaction.transaction_time <= dt_end
        )

        tx_res = await session.execute(tx_stmt)
        tx_row = tx_res.one()
        gmv = float(tx_row.gmv)
        discounts = float(tx_row.discounts)
        total_tx = int(tx_row.total_tx)
        net_sales = gmv # total_amount sudah merupakan nilai final bayar
        aov = round(gmv / total_tx, 2) if total_tx > 0 else 0.0

        # 2. Total Items Sold
        items_stmt = select(
            func.coalesce(func.sum(TransactionItem.quantity), 0).label("total_items")
        ).join(
            Transaction, TransactionItem.transaction_id == Transaction.id
        ).where(
            Transaction.outlet_id == outlet_id,
            Transaction.status == "COMPLETED",
            Transaction.transaction_time >= dt_start,
            Transaction.transaction_time <= dt_end
        )

        items_res = await session.execute(items_stmt)
        total_items = int(items_res.scalar() or 0)

        return SalesOverview(
            period=DateRange(start_date=str(start_date), end_date=str(end_date)),
            total_gmv=round(gmv, 2),
            total_discounts=round(discounts, 2),
            net_sales=round(net_sales, 2),
            total_transactions=total_tx,
            average_order_value=aov,
            total_items_sold=total_items
        )

    @staticmethod
    async def get_top_products(
        session: AsyncSession,
        outlet_id: int,
        start_date: str,
        end_date: str,
        limit: int = 5,
        sort_by: str = "qty"
    ) -> List[ProductPerformanceItem]:
        dt_start = parse_date_boundary(start_date, is_end=False)
        dt_end = parse_date_boundary(end_date, is_end=True)

        order_col = func.sum(TransactionItem.quantity) if sort_by == "qty" else func.sum(TransactionItem.subtotal)

        stmt = select(
            TransactionItem.product_name,
            func.sum(TransactionItem.quantity).label("total_qty"),
            func.sum(TransactionItem.subtotal).label("total_revenue")
        ).join(
            Transaction, TransactionItem.transaction_id == Transaction.id
        ).where(
            Transaction.outlet_id == outlet_id,
            Transaction.status == "COMPLETED",
            Transaction.transaction_time >= dt_start,
            Transaction.transaction_time <= dt_end
        ).group_by(
            TransactionItem.product_name
        ).order_by(
            desc(order_col)
        ).limit(limit)

        res = await session.execute(stmt)
        rows = res.all()

        # Dapatkan total revenue keseluruhan untuk persentase
        total_rev_all = sum(float(r.total_revenue) for r in rows) or 1.0

        items = []
        for idx, r in enumerate(rows, start=1):
            rev = float(r.total_revenue)
            items.append(
                ProductPerformanceItem(
                    rank=idx,
                    product_name=r.product_name,
                    category_name="Menu",
                    quantity_sold=int(r.total_qty),
                    total_revenue=round(rev, 2),
                    percentage_of_total=round((rev / total_rev_all) * 100, 1)
                )
            )
        return items

    @staticmethod
    async def get_slow_moving_products(
        session: AsyncSession,
        outlet_id: int,
        start_date: str,
        end_date: str,
        limit: int = 5
    ) -> List[ProductPerformanceItem]:
        dt_start = parse_date_boundary(start_date, is_end=False)
        dt_end = parse_date_boundary(end_date, is_end=True)

        stmt = select(
            TransactionItem.product_name,
            func.sum(TransactionItem.quantity).label("total_qty"),
            func.sum(TransactionItem.subtotal).label("total_revenue")
        ).join(
            Transaction, TransactionItem.transaction_id == Transaction.id
        ).where(
            Transaction.outlet_id == outlet_id,
            Transaction.status == "COMPLETED",
            Transaction.transaction_time >= dt_start,
            Transaction.transaction_time <= dt_end
        ).group_by(
            TransactionItem.product_name
        ).order_by(
            asc(func.sum(TransactionItem.quantity))
        ).limit(limit)

        res = await session.execute(stmt)
        rows = res.all()

        items = []
        for idx, r in enumerate(rows, start=1):
            items.append(
                ProductPerformanceItem(
                    rank=idx,
                    product_name=r.product_name,
                    category_name="Menu",
                    quantity_sold=int(r.total_qty),
                    total_revenue=round(float(r.total_revenue), 2),
                    percentage_of_total=0.0
                )
            )
        return items

    @staticmethod
    async def get_peak_hours(
        session: AsyncSession,
        outlet_id: int,
        date_str: str
    ) -> List[PeakHourItem]:
        dt_start = parse_date_boundary(date_str, is_end=False)
        dt_end = parse_date_boundary(date_str, is_end=True)

        # Mengambil transaksi pada hari tersebut
        stmt = select(
            Transaction.transaction_time,
            Transaction.total_amount
        ).where(
            Transaction.outlet_id == outlet_id,
            Transaction.status == "COMPLETED",
            Transaction.transaction_time >= dt_start,
            Transaction.transaction_time <= dt_end
        )

        res = await session.execute(stmt)
        rows = res.all()

        hourly_counts: Dict[int, int] = {h: 0 for h in range(24)}
        hourly_rev: Dict[int, float] = {h: 0.0 for h in range(24)}
        total_tx = len(rows)

        for tx_time, amount in rows:
            hour = tx_time.hour
            hourly_counts[hour] += 1
            hourly_rev[hour] += float(amount)

        # Filter hanya jam dengan transaksi atau jam operasional F&B (08:00 - 23:00)
        peak_items = []
        for h in range(7, 24):
            count = hourly_counts[h]
            rev = hourly_rev[h]
            pct = round((count / total_tx) * 100, 1) if total_tx > 0 else 0.0
            peak_items.append(
                PeakHourItem(
                    hour=h,
                    hour_label=f"{h:02d}:00 - {h+1:02d}:00",
                    transaction_count=count,
                    total_revenue=round(rev, 2),
                    percentage_of_orders=pct
                )
            )
        
        # Urutkan berdasarkan transaksi terbanyak
        peak_items.sort(key=lambda x: x.transaction_count, reverse=True)
        return peak_items

    @staticmethod
    async def compare_sales_period(
        session: AsyncSession,
        outlet_id: int,
        period_a_start: str,
        period_a_end: str,
        period_b_start: str,
        period_b_end: str
    ) -> PeriodComparison:
        overview_a = await SalesAnalyticsEngine.get_sales_overview(
            session, outlet_id, period_a_start, period_a_end
        )
        overview_b = await SalesAnalyticsEngine.get_sales_overview(
            session, outlet_id, period_b_start, period_b_end
        )

        gmv_a = overview_a.total_gmv
        gmv_b = overview_b.total_gmv
        orders_a = overview_a.total_transactions
        orders_b = overview_b.total_transactions

        # Hitung pertumbuhan dari Periode B ke Periode A (A adalah periode terbaru)
        if gmv_b > 0:
            gmv_growth = round(((gmv_a - gmv_b) / gmv_b) * 100, 2)
        else:
            gmv_growth = 100.0 if gmv_a > 0 else 0.0

        if orders_b > 0:
            orders_growth = round(((orders_a - orders_b) / orders_b) * 100, 2)
        else:
            orders_growth = 100.0 if orders_a > 0 else 0.0

        if gmv_growth > 2.0:
            trend = "NAIK"
        elif gmv_growth < -2.0:
            trend = "TURUN"
        else:
            trend = "STABIL"

        return PeriodComparison(
            period_a_label=f"{period_a_start} s/d {period_a_end}",
            period_a_gmv=gmv_a,
            period_a_orders=orders_a,
            period_b_label=f"{period_b_start} s/d {period_b_end}",
            period_b_gmv=gmv_b,
            period_b_orders=orders_b,
            gmv_growth_percentage=gmv_growth,
            orders_growth_percentage=orders_growth,
            trend=trend
        )

    @staticmethod
    async def get_payment_method_stats(
        session: AsyncSession,
        outlet_id: int,
        start_date: str,
        end_date: str
    ) -> List[PaymentMethodItem]:
        dt_start = parse_date_boundary(start_date, is_end=False)
        dt_end = parse_date_boundary(end_date, is_end=True)

        stmt = select(
            Transaction.payment_method,
            func.count(Transaction.id).label("total_tx"),
            func.sum(Transaction.total_amount).label("total_revenue")
        ).where(
            Transaction.outlet_id == outlet_id,
            Transaction.status == "COMPLETED",
            Transaction.transaction_time >= dt_start,
            Transaction.transaction_time <= dt_end
        ).group_by(
            Transaction.payment_method
        ).order_by(
            desc(func.count(Transaction.id))
        )

        res = await session.execute(stmt)
        rows = res.all()

        total_all_tx = sum(int(r.total_tx) for r in rows) or 1
        items = []
        for r in rows:
            tx_cnt = int(r.total_tx)
            items.append(
                PaymentMethodItem(
                    payment_method=r.payment_method or "LAINNYA",
                    transaction_count=tx_cnt,
                    total_revenue=round(float(r.total_revenue), 2),
                    percentage=round((tx_cnt / total_all_tx) * 100, 1)
                )
            )
        return items

    @staticmethod
    async def get_daily_sales_trend(
        session: AsyncSession,
        outlet_id: int,
        start_date: str,
        end_date: str
    ) -> List[DailySalesTrendItem]:
        dt_start = parse_date_boundary(start_date, is_end=False)
        dt_end = parse_date_boundary(end_date, is_end=True)

        stmt = select(
            Transaction.transaction_time,
            Transaction.total_amount
        ).where(
            Transaction.outlet_id == outlet_id,
            Transaction.status == "COMPLETED",
            Transaction.transaction_time >= dt_start,
            Transaction.transaction_time <= dt_end
        ).order_by(Transaction.transaction_time.asc())

        res = await session.execute(stmt)
        rows = res.all()

        daily_data: Dict[str, Dict[str, Any]] = {}
        day_names_id = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]

        for tx_time, amount in rows:
            d_key = tx_time.strftime("%Y-%m-%d")
            if d_key not in daily_data:
                daily_data[d_key] = {
                    "day_name": day_names_id[tx_time.weekday()],
                    "gmv": 0.0,
                    "orders": 0
                }
            daily_data[d_key]["gmv"] += float(amount)
            daily_data[d_key]["orders"] += 1

        trend_items = []
        for d_str in sorted(daily_data.keys()):
            val = daily_data[d_str]
            orders = val["orders"]
            gmv = val["gmv"]
            aov = round(gmv / orders, 2) if orders > 0 else 0.0
            trend_items.append(
                DailySalesTrendItem(
                    date=d_str,
                    day_name=val["day_name"],
                    gmv=round(gmv, 2),
                    transactions=orders,
                    aov=aov
                )
            )
        return trend_items

    @staticmethod
    async def get_category_contribution(
        session: AsyncSession,
        outlet_id: int,
        start_date: str,
        end_date: str
    ) -> List[CategoryContributionItem]:
        dt_start = parse_date_boundary(start_date, is_end=False)
        dt_end = parse_date_boundary(end_date, is_end=True)

        stmt = select(
            func.coalesce(Category.name, "Umum").label("cat_name"),
            func.sum(TransactionItem.quantity).label("total_qty"),
            func.sum(TransactionItem.subtotal).label("total_rev")
        ).join(
            Transaction, TransactionItem.transaction_id == Transaction.id
        ).outerjoin(
            Product, TransactionItem.product_id == Product.id
        ).outerjoin(
            Category, Product.category_id == Category.id
        ).where(
            Transaction.outlet_id == outlet_id,
            Transaction.status == "COMPLETED",
            Transaction.transaction_time >= dt_start,
            Transaction.transaction_time <= dt_end
        ).group_by(
            func.coalesce(Category.name, "Umum")
        ).order_by(
            desc(func.sum(TransactionItem.subtotal))
        )

        res = await session.execute(stmt)
        rows = res.all()

        total_rev_all = sum(float(r.total_rev) for r in rows) or 1.0
        results = []
        for r in rows:
            rev = float(r.total_rev)
            results.append(
                CategoryContributionItem(
                    category_name=r.cat_name,
                    quantity_sold=int(r.total_qty),
                    total_revenue=round(rev, 2),
                    percentage_of_revenue=round((rev / total_rev_all) * 100, 1)
                )
            )
        return results

    @staticmethod
    async def get_day_of_week_analysis(
        session: AsyncSession,
        outlet_id: int,
        start_date: str,
        end_date: str
    ) -> DayOfWeekAnalysis:
        trend = await SalesAnalyticsEngine.get_daily_sales_trend(
            session, outlet_id, start_date, end_date
        )

        weekday_gmvs = []
        weekday_orders = 0
        weekend_gmvs = []
        weekend_orders = 0

        for item in trend:
            # 0..3: Senin-Kamis (Weekday), 4..6: Jumat-Minggu (Weekend)
            d_obj = datetime.strptime(item.date, "%Y-%m-%d").date()
            if d_obj.weekday() in [4, 5, 6]: # Jumat, Sabtu, Minggu
                weekend_gmvs.append(item.gmv)
                weekend_orders += item.transactions
            else:
                weekday_gmvs.append(item.gmv)
                weekday_orders += item.transactions

        weekday_avg = sum(weekday_gmvs) / len(weekday_gmvs) if weekday_gmvs else 0.0
        weekend_avg = sum(weekend_gmvs) / len(weekend_gmvs) if weekend_gmvs else 0.0

        if weekday_avg > 0:
            lift = round(((weekend_avg - weekday_avg) / weekday_avg) * 100, 1)
        else:
            lift = 100.0 if weekend_avg > 0 else 0.0

        if lift > 15.0:
            insight = f"Omzet akhir pekan (Jumat-Minggu) melesat +{lift}% lebih tinggi dibanding hari kerja. Rekomendasi: tambah shift barista/kasir di Jumat-Minggu."
        elif lift < -10.0:
            insight = f"Penjualan hari kerja lebih tinggi ({abs(lift)}% lebih tinggi dari weekend). Pertimbangkan promo khusus akhir pekan untuk menarik pelanggan."
        else:
            insight = "Performa penjualan hari kerja dan akhir pekan relatif seimbang dan stabil."

        return DayOfWeekAnalysis(
            weekday_avg_daily_gmv=round(weekday_avg, 2),
            weekday_total_orders=weekday_orders,
            weekend_avg_daily_gmv=round(weekend_avg, 2),
            weekend_total_orders=weekend_orders,
            weekend_lift_percentage=lift,
            insight=insight
        )

    @staticmethod
    async def get_basket_size_distribution(
        session: AsyncSession,
        outlet_id: int,
        start_date: str,
        end_date: str
    ) -> List[BasketSizeItem]:
        dt_start = parse_date_boundary(start_date, is_end=False)
        dt_end = parse_date_boundary(end_date, is_end=True)

        # Hitung jumlah item per invoice
        stmt = select(
            Transaction.id,
            func.coalesce(func.sum(TransactionItem.quantity), 0).label("item_count")
        ).join(
            TransactionItem, Transaction.id == TransactionItem.transaction_id
        ).where(
            Transaction.outlet_id == outlet_id,
            Transaction.status == "COMPLETED",
            Transaction.transaction_time >= dt_start,
            Transaction.transaction_time <= dt_end
        ).group_by(
            Transaction.id
        )

        res = await session.execute(stmt)
        rows = res.all()

        counts = {"1 Menu": 0, "2 Menu": 0, "3-4 Menu": 0, "5+ Menu": 0}
        total_tx = len(rows)

        for _, count in rows:
            if count == 1:
                counts["1 Menu"] += 1
            elif count == 2:
                counts["2 Menu"] += 1
            elif 3 <= count <= 4:
                counts["3-4 Menu"] += 1
            else:
                counts["5+ Menu"] += 1

        results = []
        for label, cnt in counts.items():
            pct = round((cnt / total_tx) * 100, 1) if total_tx > 0 else 0.0
            results.append(
                BasketSizeItem(
                    basket_size_label=label,
                    order_count=cnt,
                    percentage=pct
                )
            )
        return results
