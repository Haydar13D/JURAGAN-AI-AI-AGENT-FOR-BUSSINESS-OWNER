from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

class DateRange(BaseModel):
    start_date: str
    end_date: str

class SalesOverview(BaseModel):
    period: DateRange
    total_gmv: float = Field(..., description="Total Gross Merchandise Value / Omzet Kotor")
    total_discounts: float = Field(0.0, description="Total Potongan Diskon")
    net_sales: float = Field(..., description="Omzet Bersih setelah Diskon")
    total_transactions: int = Field(..., description="Jumlah Nota / Struk")
    average_order_value: float = Field(..., description="Rata-rata Nilai Belanja per Transaksi (AOV)")
    total_items_sold: int = Field(..., description="Total Produk Terjual (Pcs/Cup)")

class ProductPerformanceItem(BaseModel):
    rank: int
    product_name: str
    category_name: Optional[str] = "Umum"
    quantity_sold: int
    total_revenue: float
    percentage_of_total: float = 0.0

class PeakHourItem(BaseModel):
    hour: int
    hour_label: str
    transaction_count: int
    total_revenue: float
    percentage_of_orders: float = 0.0

class PaymentMethodItem(BaseModel):
    payment_method: str
    transaction_count: int
    total_revenue: float
    percentage: float

class PeriodComparison(BaseModel):
    period_a_label: str
    period_a_gmv: float
    period_a_orders: int
    period_b_label: str
    period_b_gmv: float
    period_b_orders: int
    gmv_growth_percentage: float
    orders_growth_percentage: float
    trend: str # NAIK, TURUN, STABIL

class DailySalesTrendItem(BaseModel):
    date: str
    day_name: str
    gmv: float
    transactions: int
    aov: float

class CategoryContributionItem(BaseModel):
    category_name: str
    quantity_sold: int
    total_revenue: float
    percentage_of_revenue: float

class DayOfWeekAnalysis(BaseModel):
    weekday_avg_daily_gmv: float
    weekday_total_orders: int
    weekend_avg_daily_gmv: float
    weekend_total_orders: int
    weekend_lift_percentage: float
    insight: str

class BasketSizeItem(BaseModel):
    basket_size_label: str
    order_count: int
    percentage: float
