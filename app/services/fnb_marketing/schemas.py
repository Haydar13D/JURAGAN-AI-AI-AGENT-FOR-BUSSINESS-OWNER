"""
app/services/fnb_marketing/schemas.py
Pydantic data schemas for the F&B Sales-to-Marketing Vertical (FR-16).
Defines input, output, and configuration models for every tool in the F&B workflow.
"""

from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


# =====================================================================
# 1. POS INGESTION MODELS
# =====================================================================

class ItemSalesStats(BaseModel):
    product_name: str
    category: str
    unit_price: float
    baseline_qty: int = 0
    current_qty: int = 0
    qty_change_pct: float = 0.0
    baseline_revenue: float = 0.0
    current_revenue: float = 0.0


class PosDataSummary(BaseModel):
    total_transactions: int
    total_revenue: float
    baseline_period_label: str
    current_period_label: str
    items: List[ItemSalesStats]
    analyzed_products_count: int


# =====================================================================
# 2. OPPORTUNITY DETECTION MODELS
# =====================================================================

class OpportunityItem(BaseModel):
    opportunity_id: str
    product_name: str
    category: str
    signal: str  # "SHARP_DROP", "SLOW_MOVING_HIGH_MARGIN", "POPULAR_SPIKE"
    score: float
    evidence: Dict[str, Any]
    unit_price: float
    margin_pct: float = 62.5
    estimated_lost_revenue: float = 0.0
    status: str = "DETECTED"


class BatchOpportunities(BaseModel):
    opportunities: List[OpportunityItem]
    detected_count: int


# =====================================================================
# 3. CAMPAIGN PLANNING MODELS
# =====================================================================

class CampaignPlanItem(BaseModel):
    plan_id: str
    opportunity_id: str
    product_name: str
    category: str
    headline: str
    strategy_rationale: str
    target_audience: str
    original_price: float
    discount_pct: float
    promo_price: float
    duration_days: int
    caption: str


class BatchCampaignPlans(BaseModel):
    plans: List[CampaignPlanItem]
    total_plans: int


# =====================================================================
# 4. FLYER ASSET GENERATION MODELS
# =====================================================================

class FlyerAssetItem(BaseModel):
    asset_id: str
    plan_id: str
    product_name: str
    flyer_url: str
    flyer_svg_content: Optional[str] = None
    engine_used: str = "svg"
    dimensions: str = "1080x1080"


class BatchFlyerAssets(BaseModel):
    assets: List[FlyerAssetItem]
    plans: List[CampaignPlanItem]


# =====================================================================
# 5. GOVERNANCE & PUBLISHING MODELS
# =====================================================================

class ApprovedCampaignPayload(BaseModel):
    plans: List[CampaignPlanItem]
    assets: List[FlyerAssetItem]
    is_approved: bool = True
    approved_by: Optional[str] = None
    approval_timestamp: Optional[str] = None


class PublishResult(BaseModel):
    post_id: str
    platform: str = "instagram"
    is_sandbox: bool = True
    product_name: str
    caption: str
    flyer_url: str
    published_at: str
    status: str = "MOCK_PUBLISHED"
    summary: str = ""
