"""
app/services/retail_pricing/schemas.py
Pydantic data schemas for the Used-iPhone Retail Pricing Vertical.
Defines input, output, and configuration models for every tool in the retail workflow.
"""

from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


# =====================================================================
# 1. RAW & NORMALIZED MODELS
# =====================================================================

class RawVendorRow(BaseModel):
    vendor_row_id: str
    vendor_id: str
    source_timestamp: str
    raw_model: str
    raw_storage: str
    raw_color: str
    raw_condition: str
    raw_distribution: str
    raw_warranty: str
    raw_battery: str
    raw_cost: str
    stock: int = 1
    currency: str = "IDR"


class NormalizedProduct(BaseModel):
    item_id: str
    vendor_id: str
    model: str                      # Normalized, e.g. "iPhone 11", "iPhone 13 Pro Max"
    storage_gb: int                 # Normalized integer, e.g. 64, 128, 256, 512
    color: str                      # Normalized, e.g. "Black", "Sierra Blue"
    condition_grade: str            # "A" (Like New / Mulus), "B" (Lecet / Grade B), "C" (Minus)
    distribution_label: str         # "iBox" (Resmi Indonesia), "Inter" (Global), "Unknown"
    warranty_status: str            # "Active", "Expired", "Unknown"
    battery_health: Optional[int] = None  # Percentage, e.g. 85, or None if unknown
    cost: int                       # Cleaned integer IDR cost
    stock: int = 1
    currency: str = "IDR"
    is_valid_row: bool = True
    quarantine_reason: Optional[str] = None


class BatchNormalizedProducts(BaseModel):
    items: List[NormalizedProduct]
    total_rows: int = 0
    quarantined_count: int = 0


# =====================================================================
# 2. MARKET REFERENCE & MATCHING MODELS
# =====================================================================

class MarketReference(BaseModel):
    model: str
    storage_gb: int
    distribution_label: str
    condition_grade: str
    observed_price: int
    source: str
    observed_at: str
    confidence: float = 1.0


class MatchedReferenceProduct(BaseModel):
    item: NormalizedProduct
    has_market_reference: bool = False
    market_price: Optional[int] = None
    market_source: Optional[str] = None
    market_confidence: float = 0.0
    market_observed_at: Optional[str] = None
    is_stale_market_data: bool = False


class BatchMatchedReferences(BaseModel):
    items: List[MatchedReferenceProduct]


# =====================================================================
# 3. DETERMINISTIC PRICING MODELS
# =====================================================================

class PricedProduct(BaseModel):
    item: NormalizedProduct
    market_ref: Optional[MatchedReferenceProduct] = None
    total_cost: int
    target_gross_margin: float
    raw_min_price: float
    recommended_selling_price: int
    expected_margin: float
    markup: float


class BatchPricedProducts(BaseModel):
    items: List[PricedProduct]


# =====================================================================
# 4. MARKET COMPARISON & REVIEW FLAGS
# =====================================================================

class ReviewFlag(str):
    CLEAN = "CLEAN"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    ABOVE_MARKET = "ABOVE_MARKET"
    LOW_BATTERY = "LOW_BATTERY"
    STALE_MARKET_REF = "STALE_MARKET_REF"
    MISSING_MARKET_REF = "MISSING_MARKET_REF"
    QUARANTINED = "QUARANTINED"


class EvaluatedProduct(BaseModel):
    pricing: PricedProduct
    market_diff_amount: Optional[int] = None
    market_premium_ratio: Optional[float] = None
    review_flags: List[str] = Field(default_factory=list)
    final_status: str = "APPROVED_CANDIDATE"  # or "NEEDS_REVIEW"


class BatchEvaluatedCatalog(BaseModel):
    items: List[EvaluatedProduct]
    total_items: int = 0
    clean_items_count: int = 0
    flagged_items_count: int = 0


# =====================================================================
# 5. ACTION APPROVAL & EXPORT MODELS
# =====================================================================

class ApprovedCatalogPayload(BaseModel):
    catalog: BatchEvaluatedCatalog
    is_approved: bool = True
    approved_by: Optional[str] = None
    approval_timestamp: Optional[str] = None


class ExportResult(BaseModel):
    file_format: str = "csv"
    exported_rows_count: int = 0
    export_csv_content: str = ""
    summary: str = ""
