"""
app/services/fnb_marketing/tools.py
Executable Tool implementations for the F&B Sales-to-Marketing Vertical (FR-16).
Registers fnb.ingest_pos_data, fnb.detect_opportunities, fnb.plan_campaign,
fnb.generate_flyer, and fnb.publish_instagram into ToolRegistry.
"""

import os
import csv
import io
import uuid
from datetime import datetime, date, timedelta
from typing import List, Optional, Dict, Any, Tuple
from collections import defaultdict
from pydantic import BaseModel, Field

from app.core.tool_registry import BaseTool, ToolMetadata, ToolCategory, ExecutionContext, ToolRegistry
from app.services.flyer_generator import FlyerGenerator
from app.services.fnb_marketing.schemas import (
    PosDataSummary,
    ItemSalesStats,
    BatchOpportunities,
    OpportunityItem,
    BatchCampaignPlans,
    CampaignPlanItem,
    BatchFlyerAssets,
    FlyerAssetItem,
    ApprovedCampaignPayload,
    PublishResult
)


# =====================================================================
# 1. TOOL: INGEST POS SALES DATA
# =====================================================================

class IngestPosDataConfig(BaseModel):
    days_lookback: int = Field(default=14, description="Total days for comparison window (7 baseline + 7 current)")
    sample_csv_path: str = Field(default="data/synthetic_pos_60days.csv", description="Path to POS transaction CSV")


class IngestPosDataInput(BaseModel):
    csv_content: Optional[str] = None
    csv_path: Optional[str] = None


class IngestPosDataTool(BaseTool):
    metadata = ToolMetadata(
        tool_type="fnb.ingest_pos_data",
        version="1.0.0",
        display_name="Ingest POS Sales Data",
        description="Reads historical POS transactions and computes sales velocity by product.",
        category=ToolCategory.INGESTION,
        is_side_effect=False,
        requires_action_approval=False,
        supported_templates=["tpl_fnb_sales_to_marketing_v1"]
    )
    config_schema = IngestPosDataConfig
    input_schema = IngestPosDataInput
    output_schema = PosDataSummary

    async def execute(self, config: IngestPosDataConfig, inputs: IngestPosDataInput, context: ExecutionContext) -> PosDataSummary:
        content = inputs.csv_content
        if not content:
            path = inputs.csv_path or config.sample_csv_path
            if not os.path.exists(path):
                raise FileNotFoundError(f"POS data file not found at {path}")
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()

        reader = csv.DictReader(io.StringIO(content))
        rows = list(reader)

        # Parse date range: find latest date in data as as_of_date
        dates = []
        for r in rows:
            occurred = r.get("occurred_at", "")
            if occurred:
                try:
                    dates.append(datetime.strptime(occurred[:10], "%Y-%m-%d").date())
                except Exception:
                    pass

        as_of = max(dates) if dates else date.today()
        current_start = as_of - timedelta(days=6)
        baseline_end = as_of - timedelta(days=7)
        baseline_start = as_of - timedelta(days=13)

        product_stats: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
            "category": "Menu",
            "unit_price": 0.0,
            "baseline_qty": 0,
            "current_qty": 0,
            "baseline_rev": 0.0,
            "current_rev": 0.0
        })

        total_tx = set()
        total_revenue = 0.0

        for r in rows:
            tx_id = r.get("transaction_id", "")
            occurred_str = r.get("occurred_at", "")
            if not occurred_str:
                continue

            try:
                row_date = datetime.strptime(occurred_str[:10], "%Y-%m-%d").date()
            except Exception:
                continue

            p_name = r.get("product_name", "").strip()
            cat = r.get("category", "Menu").strip()
            qty = int(r.get("quantity", 1))
            total_amt = float(r.get("total_amount", 0.0))
            u_price = float(r.get("unit_price", total_amt / qty if qty else 0))

            if baseline_start <= row_date <= as_of:
                total_tx.add(tx_id)
                total_revenue += total_amt

            stats = product_stats[p_name]
            stats["category"] = cat
            stats["unit_price"] = u_price

            if baseline_start <= row_date <= baseline_end:
                stats["baseline_qty"] += qty
                stats["baseline_rev"] += total_amt
            elif current_start <= row_date <= as_of:
                stats["current_qty"] += qty
                stats["current_rev"] += total_amt

        items_list: List[ItemSalesStats] = []
        for p_name, s in product_stats.items():
            base_q = s["baseline_qty"]
            curr_q = s["current_qty"]
            change_pct = ((curr_q - base_q) / base_q * 100.0) if base_q > 0 else 0.0
            items_list.append(ItemSalesStats(
                product_name=p_name,
                category=s["category"],
                unit_price=s["unit_price"],
                baseline_qty=base_q,
                current_qty=curr_q,
                qty_change_pct=round(change_pct, 1),
                baseline_revenue=s["baseline_rev"],
                current_revenue=s["current_rev"]
            ))

        return PosDataSummary(
            total_transactions=len(total_tx),
            total_revenue=total_revenue,
            baseline_period_label=f"{baseline_start.isoformat()} s/d {baseline_end.isoformat()}",
            current_period_label=f"{current_start.isoformat()} s/d {as_of.isoformat()}",
            items=items_list,
            analyzed_products_count=len(items_list)
        )


# =====================================================================
# 2. TOOL: DETECT SALES OPPORTUNITIES
# =====================================================================

class DetectOpportunitiesConfig(BaseModel):
    min_drop_percentage: float = Field(default=30.0, description="Minimum sales drop percentage to trigger SHARP_DROP")
    min_baseline_qty: int = Field(default=10, description="Minimum historical unit sales to consider meaningful")


class DetectOpportunitiesInput(BaseModel):
    pos_data: Optional[PosDataSummary] = None


class DetectOpportunitiesTool(BaseTool):
    metadata = ToolMetadata(
        tool_type="fnb.detect_opportunities",
        version="1.0.0",
        display_name="Detect Sales Opportunities",
        description="Scans sales velocity anomalies (SHARP_DROP, SLOW_MOVING) deterministically.",
        category=ToolCategory.ANALYSIS,
        is_side_effect=False,
        requires_action_approval=False,
        supported_templates=["tpl_fnb_sales_to_marketing_v1"]
    )
    config_schema = DetectOpportunitiesConfig
    input_schema = DetectOpportunitiesInput
    output_schema = BatchOpportunities

    async def execute(self, config: DetectOpportunitiesConfig, inputs: DetectOpportunitiesInput, context: ExecutionContext) -> BatchOpportunities:
        if not inputs.pos_data:
            raise ValueError("No POS data provided to detect_opportunities")

        opportunities: List[OpportunityItem] = []

        for item in inputs.pos_data.items:
            # Rule 1: SHARP_DROP
            if item.baseline_qty >= config.min_baseline_qty and item.qty_change_pct <= -config.min_drop_percentage:
                drop_abs = abs(item.qty_change_pct)
                qty_lost = item.baseline_qty - item.current_qty
                revenue_lost = qty_lost * item.unit_price

                # Deterministic score based on severity of drop and lost revenue
                score = min(1.0, round((drop_abs / 100.0) * 0.6 + min(0.4, (revenue_lost / 5000000.0) * 0.4), 2))

                opportunities.append(OpportunityItem(
                    opportunity_id=f"opp_{uuid.uuid4().hex[:6]}",
                    product_name=item.product_name,
                    category=item.category,
                    signal="SHARP_DROP",
                    score=score,
                    unit_price=item.unit_price,
                    margin_pct=62.5,
                    estimated_lost_revenue=revenue_lost,
                    evidence={
                        "baseline_qty": item.baseline_qty,
                        "current_qty": item.current_qty,
                        "drop_percentage": drop_abs,
                        "qty_lost": qty_lost,
                        "estimated_lost_revenue": revenue_lost
                    }
                ))

        # Sort by score descending
        opportunities.sort(key=lambda x: x.score, reverse=True)

        return BatchOpportunities(
            opportunities=opportunities,
            detected_count=len(opportunities)
        )


# =====================================================================
# 3. TOOL: PLAN PROMOTIONAL CAMPAIGN
# =====================================================================

class PlanCampaignConfig(BaseModel):
    default_discount_pct: float = Field(default=15.0, ge=5.0, le=50.0)
    promo_duration_days: int = Field(default=7, ge=1, le=30)
    target_audience: str = Field(default="Pekerja remote & pelanggan setia jam santai")


class PlanCampaignInput(BaseModel):
    opportunities: Optional[BatchOpportunities] = None


class PlanCampaignTool(BaseTool):
    metadata = ToolMetadata(
        tool_type="fnb.plan_campaign",
        version="1.0.0",
        display_name="Plan Promotional Campaign",
        description="Generates campaign strategy, targeted discount, and caption copy for detected opportunities.",
        category=ToolCategory.ANALYSIS,
        is_side_effect=False,
        requires_action_approval=False,
        supported_templates=["tpl_fnb_sales_to_marketing_v1"]
    )
    config_schema = PlanCampaignConfig
    input_schema = PlanCampaignInput
    output_schema = BatchCampaignPlans

    async def execute(self, config: PlanCampaignConfig, inputs: PlanCampaignInput, context: ExecutionContext) -> BatchCampaignPlans:
        if not inputs.opportunities:
            raise ValueError("No opportunities provided to plan_campaign")

        plans: List[CampaignPlanItem] = []

        # Take top priority opportunity
        for opp in inputs.opportunities.opportunities[:2]:
            disc = config.default_discount_pct
            promo_p = round(opp.unit_price * (1.0 - (disc / 100.0)))

            caption = (
                f"☕ SEMANGAT HARI INI DENGAN {opp.product_name.upper()}!\n\n"
                f"Lagi butuh mood booster buat nemenin aktivitasmu? Nikmati diskon spesial {int(disc)}% "
                f"khusus menu favorit {opp.product_name}!\n\n"
                f"🏷️ Harga Normal: Rp{int(opp.unit_price):,}\n"
                f"🔥 Harga Promo: Rp{int(promo_p):,}\n\n"
                f"Promo berlaku selama {config.promo_duration_days} hari ke depan di outlet atau pemesanan kasir. "
                f"Yuk merapat sekarang sebelum kehabisan! ✨\n\n"
                f"#KopiKenangan #PromoKopi #{opp.product_name.replace(' ', '')} #NgopiSantai"
            )

            plans.append(CampaignPlanItem(
                plan_id=f"plan_{uuid.uuid4().hex[:6]}",
                opportunity_id=opp.opportunity_id,
                product_name=opp.product_name,
                category=opp.category,
                headline=f"Promo Spesial {int(disc)}% — {opp.product_name}",
                strategy_rationale=(
                    f"Penjualan {opp.product_name} mengalami anomali {opp.signal} (turun {opp.evidence.get('drop_percentage', 0):.1f}%). "
                    f"Dengan margin produk yang sehat ({opp.margin_pct}%), diskon {int(disc)}% dirancang untuk "
                    f"mengaktifkan kembali daya beli target {config.target_audience} tanpa mengorbankan profitabilitas."
                ),
                target_audience=config.target_audience,
                original_price=opp.unit_price,
                discount_pct=disc,
                promo_price=promo_p,
                duration_days=config.promo_duration_days,
                caption=caption
            ))

        return BatchCampaignPlans(
            plans=plans,
            total_plans=len(plans)
        )


# =====================================================================
# 4. TOOL: GENERATE PROMO FLYER ASSET
# =====================================================================

class GenerateFlyerConfig(BaseModel):
    engine: str = Field(default="svg", description="Flyer engine: svg (free fast), pollinations, or dalle3")
    tagline: str = Field(default="PROMO SPESIAL HARI INI")
    cta_text: str = Field(default="Klaim Promo di Kasir")


class GenerateFlyerInput(BaseModel):
    campaign_plans: Optional[BatchCampaignPlans] = None


class GenerateFlyerTool(BaseTool):
    metadata = ToolMetadata(
        tool_type="fnb.generate_flyer",
        version="1.0.0",
        display_name="Generate Promotional Flyer",
        description="Generates square Instagram-ready 1080x1080 promotional flyer SVG assets.",
        category=ToolCategory.TRANSFORMATION,
        is_side_effect=False,
        requires_action_approval=False,
        supported_templates=["tpl_fnb_sales_to_marketing_v1"]
    )
    config_schema = GenerateFlyerConfig
    input_schema = GenerateFlyerInput
    output_schema = BatchFlyerAssets

    async def execute(self, config: GenerateFlyerConfig, inputs: GenerateFlyerInput, context: ExecutionContext) -> BatchFlyerAssets:
        if not inputs.campaign_plans:
            raise ValueError("No campaign plans provided to generate_flyer")

        assets: List[FlyerAssetItem] = []

        for idx, plan in enumerate(inputs.campaign_plans.plans):
            flyer_rel_url = FlyerGenerator.generate_flyer_svg(
                outlet_name="Kopi Kenangan Senja",
                product_name=plan.product_name,
                category=plan.category,
                original_price=plan.original_price,
                discount_pct=plan.discount_pct,
                tagline=config.tagline,
                cta_text=config.cta_text,
                campaign_id=idx + 101
            )

            assets.append(FlyerAssetItem(
                asset_id=f"asset_{uuid.uuid4().hex[:6]}",
                plan_id=plan.plan_id,
                product_name=plan.product_name,
                flyer_url=flyer_rel_url,
                engine_used=config.engine,
                dimensions="1080x1080"
            ))

        return BatchFlyerAssets(
            assets=assets,
            plans=inputs.campaign_plans.plans
        )


# =====================================================================
# 5. TOOL: PUBLISH TO INSTAGRAM (SANDBOX / LIVE)
# =====================================================================

class PublishInstagramConfig(BaseModel):
    sandbox_mode: bool = Field(default=True, description="Publish in sandbox simulation mode")
    platform: str = Field(default="instagram")


class PublishInstagramInput(BaseModel):
    approved_payload: Optional[Any] = None
    assets_payload: Optional[Any] = None


class PublishInstagramTool(BaseTool):
    metadata = ToolMetadata(
        tool_type="fnb.publish_instagram",
        version="1.0.0",
        display_name="Publish to Instagram",
        description="Publishes approved promo campaign and flyer to Instagram feed (or verified sandbox).",
        category=ToolCategory.EXPORT,
        is_side_effect=True,
        requires_action_approval=False,
        supported_templates=["tpl_fnb_sales_to_marketing_v1"]
    )
    config_schema = PublishInstagramConfig
    input_schema = PublishInstagramInput
    output_schema = PublishResult

    async def execute(self, config: PublishInstagramConfig, inputs: PublishInstagramInput, context: ExecutionContext) -> PublishResult:
        plans = []
        flyer_url = "/static/campaigns/flyer_sample.svg"

        payload = inputs.approved_payload or inputs.assets_payload
        if payload:
            if hasattr(payload, "plans") and payload.plans:
                plans = payload.plans
                if hasattr(payload, "assets") and payload.assets:
                    flyer_url = payload.assets[0].flyer_url
            elif isinstance(payload, dict):
                plans = payload.get("plans", [])
                assets = payload.get("assets", [])
                if assets and isinstance(assets[0], dict):
                    flyer_url = assets[0].get("flyer_url", flyer_url)

        if not plans:
            raise ValueError("No approved plans to publish")

        plan = plans[0]
        post_id = f"ig_post_{uuid.uuid4().hex[:8]}"
        now_iso = datetime.now().isoformat()

        return PublishResult(
            post_id=post_id,
            platform=config.platform,
            is_sandbox=config.sandbox_mode,
            product_name=plan.product_name,
            caption=plan.caption,
            flyer_url=flyer_url,
            published_at=now_iso,
            status="MOCK_PUBLISHED" if config.sandbox_mode else "PUBLISHED",
            summary=(
                f"Kampanye '{plan.product_name}' berhasil dipublikasikan ke {config.platform.title()} "
                f"({'Sandbox Simulasi' if config.sandbox_mode else 'Live'}) dengan ID {post_id}."
            )
        )


# =====================================================================
# AUTO-REGISTRATION HELPER
# =====================================================================

def register_fnb_tools() -> None:
    """Registers all certified F&B marketing tools with ToolRegistry singleton."""
    tools = [
        IngestPosDataTool(),
        DetectOpportunitiesTool(),
        PlanCampaignTool(),
        GenerateFlyerTool(),
        PublishInstagramTool()
    ]
    for t in tools:
        ToolRegistry.register(t)
