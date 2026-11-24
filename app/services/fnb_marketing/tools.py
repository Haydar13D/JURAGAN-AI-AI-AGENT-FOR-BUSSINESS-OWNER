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
import re
from pydantic import BaseModel, Field

from app.core.tool_registry import BaseTool, ToolMetadata, ToolCategory, ExecutionContext, ToolRegistry
from app.services.flyer_generator import FlyerGenerator, is_beverage_item
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

        # Tolak format laporan ringkasan tanpa tanggal transaksi (mis. Daily Report)
        first_non_empty = [ln.strip() for ln in content.strip().splitlines() if ln.strip()]
        if first_non_empty and first_non_empty[0].lower().startswith("daily report"):
            return PosDataSummary(
                status="INSUFFICIENT_DATA",
                reason="Format Daily Report tanpa kolom tanggal transaksi tidak dapat digunakan untuk analisis tren perbandingan periode."
            )

        reader = csv.DictReader(io.StringIO(content))
        rows = list(reader)
        if not rows:
            return PosDataSummary(
                status="INSUFFICIENT_DATA",
                reason="Data POS kosong atau tidak memiliki baris transaksi yang valid."
            )

        fieldnames = [f.lower().strip() for f in (reader.fieldnames or [])]
        date_cols = {"occurred_at", "tanggal", "date"}
        if not any(col in fieldnames for col in date_cols):
            return PosDataSummary(
                status="INSUFFICIENT_DATA",
                reason="Data POS tidak memiliki kolom tanggal transaksi (occurred_at / tanggal / date)."
            )

        price_cols = {"unit_price", "harga", "price", "total_amount", "total", "jumlah"}
        if not any(col in fieldnames for col in price_cols):
            return PosDataSummary(
                status="INSUFFICIENT_DATA",
                reason="Data POS tidak memiliki kolom harga atau total transaksi (unit_price / harga / total_amount)."
            )

        # Parse date range: find latest date in data as as_of_date
        dates = []
        for r in rows:
            occurred = r.get("occurred_at") or r.get("tanggal") or r.get("Tanggal") or r.get("date") or ""
            if occurred:
                try:
                    dates.append(datetime.strptime(str(occurred)[:10], "%Y-%m-%d").date())
                except Exception:
                    pass

        if not dates:
            return PosDataSummary(
                status="INSUFFICIENT_DATA",
                reason="Tidak ada tanggal transaksi yang valid (format YYYY-MM-DD) pada data POS."
            )

        as_of = max(dates)
        current_start = as_of - timedelta(days=6)
        baseline_end = as_of - timedelta(days=7)
        baseline_start = as_of - timedelta(days=13)

        product_stats: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
            "category": "Makanan",
            "unit_price": 0.0,
            "margin_pct": None,
            "baseline_qty": 0,
            "current_qty": 0,
            "baseline_rev": 0.0,
            "current_rev": 0.0
        })

        total_tx = set()
        total_revenue = 0.0

        for idx_r, r in enumerate(rows):
            tx_id = r.get("transaction_id") or r.get("id_transaksi") or f"TX-{idx_r}"
            occurred_str = r.get("occurred_at") or r.get("tanggal") or r.get("Tanggal") or r.get("date") or ""
            if not occurred_str:
                continue
            try:
                row_date = datetime.strptime(str(occurred_str)[:10], "%Y-%m-%d").date()
            except Exception:
                continue

            p_name = (
                r.get("product_name") or r.get("menu") or r.get("Menu") or
                r.get("nama_menu") or r.get("nama_produk") or r.get("item_name") or
                r.get("produk") or ""
            ).strip()
            if not p_name:
                continue

            raw_cat = (r.get("category") or r.get("kategori") or r.get("Kategori") or "").strip()
            if not raw_cat or raw_cat.lower() in ["menu", "item", "produk"]:
                is_drink_p = is_beverage_item(p_name)
                cat = "Minuman" if is_drink_p else "Makanan"
            else:
                cat = raw_cat

            try:
                qty = int(float(r.get("quantity") or r.get("qty") or 1))
            except Exception:
                qty = 1

            raw_total = r.get("total_amount") or r.get("total") or r.get("jumlah") or r.get("Jumlah") or 0.0
            try:
                total_amt = float(re.sub(r"[^\d.]", "", str(raw_total))) if raw_total else 0.0
            except Exception:
                total_amt = 0.0

            raw_uprice = r.get("unit_price") or r.get("harga") or r.get("Harga") or r.get("price") or 0.0
            try:
                u_price = float(re.sub(r"[^\d.]", "", str(raw_uprice))) if raw_uprice else (total_amt / qty if qty > 0 else 0.0)
            except Exception:
                u_price = total_amt / qty if qty > 0 else 0.0
            if u_price <= 0 and total_amt <= 0:
                continue
            if total_amt <= 0:
                total_amt = u_price * qty

            raw_margin = r.get("margin_pct") or r.get("margin")
            margin_val: Optional[float] = None
            if raw_margin not in (None, ""):
                try:
                    margin_val = float(re.sub(r"[^\d.]", "", str(raw_margin)))
                except Exception:
                    margin_val = None

            if baseline_start <= row_date <= as_of:
                total_tx.add(tx_id)
                total_revenue += total_amt

            stats = product_stats[p_name]
            stats["category"] = cat
            stats["unit_price"] = u_price
            if margin_val is not None:
                stats["margin_pct"] = margin_val

            if baseline_start <= row_date <= baseline_end:
                stats["baseline_qty"] += qty
                stats["baseline_rev"] += total_amt
            elif current_start <= row_date <= as_of:
                stats["current_qty"] += qty
                stats["current_rev"] += total_amt

        if not product_stats:
            return PosDataSummary(
                status="INSUFFICIENT_DATA",
                reason="Tidak ditemukan produk dengan harga dan tanggal transaksi yang valid pada data POS."
            )

        all_single_period = all(s["baseline_qty"] == 0 for s in product_stats.values())
        items_list: List[ItemSalesStats] = []
        for p_name, s in product_stats.items():
            base_q = s["baseline_qty"]
            curr_q = s["current_qty"]
            base_rev = s["baseline_rev"]
            curr_rev = s["current_rev"]
            change_pct = ((curr_q - base_q) / base_q * 100.0) if base_q > 0 else 0.0
            items_list.append(ItemSalesStats(
                product_name=p_name,
                category=s["category"],
                unit_price=s["unit_price"],
                margin_pct=s["margin_pct"],
                baseline_qty=base_q,
                current_qty=curr_q,
                qty_change_pct=round(change_pct, 1),
                baseline_revenue=base_rev,
                current_revenue=curr_rev
            ))

        if all_single_period:
            return PosDataSummary(
                status="INSUFFICIENT_DATA",
                reason="Data hanya mencakup satu periode (tidak ada transaksi pada periode pembanding sebelumnya untuk menghitung tren penjualan).",
                total_transactions=len(total_tx),
                total_revenue=total_revenue,
                baseline_period_label=f"{baseline_start.isoformat()} s/d {baseline_end.isoformat()}",
                current_period_label=f"{current_start.isoformat()} s/d {as_of.isoformat()}",
                items=items_list,
                analyzed_products_count=len(items_list)
            )

        return PosDataSummary(
            status="OK",
            reason=None,
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

    def apply_business_config(self, raw_config: Dict[str, Any], business_config: Dict[str, Any]) -> Dict[str, Any]:
        cfg = dict(raw_config)
        if "sales_drop_threshold_pct" in business_config:
            cfg["min_drop_percentage"] = float(business_config["sales_drop_threshold_pct"])
        return cfg

    async def execute(self, config: DetectOpportunitiesConfig, inputs: DetectOpportunitiesInput, context: ExecutionContext) -> BatchOpportunities:
        if not inputs.pos_data:
            raise ValueError("No POS data provided to detect_opportunities")

        if getattr(inputs.pos_data, "status", "OK") != "OK":
            return BatchOpportunities(
                status="INSUFFICIENT_DATA",
                reason=inputs.pos_data.reason or "Data POS tidak mencukupi untuk analisis peluang promosi.",
                opportunities=[],
                detected_count=0
            )

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
                    margin_pct=item.margin_pct,
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
            status="OK",
            opportunities=opportunities,
            detected_count=len(opportunities)
        )


# =====================================================================
# 3. TOOL: PLAN PROMOTIONAL CAMPAIGN
# =====================================================================

class PlanCampaignConfig(BaseModel):
    default_discount_pct: float = Field(default=15.0, ge=0.0, le=50.0)
    max_discount_pct: Optional[float] = Field(default=20.0, ge=0.0, le=50.0)
    minimum_gross_margin_pct: Optional[float] = Field(default=25.0, ge=0.0, le=100.0)
    promo_duration_days: int = Field(default=7, ge=1, le=30)
    target_audience: str = Field(default="Pekerja remote & pelanggan setia jam santai")
    focus_product_name: Optional[str] = Field(default=None, description="Menu makanan/minuman spesifik yang difokuskan untuk promosi")


class PlanCampaignInput(BaseModel):
    opportunities: Optional[BatchOpportunities] = None
    performance_scores: Optional[Dict[str, float]] = None


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

    def apply_business_config(self, raw_config: Dict[str, Any], business_config: Dict[str, Any]) -> Dict[str, Any]:
        cfg = dict(raw_config)
        if "max_discount_pct" in business_config:
            target_disc = float(business_config["max_discount_pct"])
            cfg["max_discount_pct"] = target_disc
            cfg["default_discount_pct"] = target_disc
        if "minimum_gross_margin_pct" in business_config:
            cfg["minimum_gross_margin_pct"] = float(business_config["minimum_gross_margin_pct"])
        if "campaign_duration_days" in business_config:
            cfg["promo_duration_days"] = int(business_config["campaign_duration_days"])
        if "target_audience" in business_config:
            cfg["target_audience"] = str(business_config["target_audience"])
        if "focus_product_name" in business_config and business_config["focus_product_name"]:
            cfg["focus_product_name"] = str(business_config["focus_product_name"])
        return cfg

    async def execute(self, config: PlanCampaignConfig, inputs: PlanCampaignInput, context: ExecutionContext) -> BatchCampaignPlans:
        if not inputs.opportunities:
            raise ValueError("No opportunities provided to plan_campaign")

        plans: List[CampaignPlanItem] = []
        max_discount = config.max_discount_pct if config.max_discount_pct is not None else 20.0
        min_margin = config.minimum_gross_margin_pct if config.minimum_gross_margin_pct is not None else 25.0

        candidate_opps = list(inputs.opportunities.opportunities)
        if inputs.performance_scores:
            from app.services.instagram_insights import prioritize_opportunities_by_performance
            candidate_opps = prioritize_opportunities_by_performance(candidate_opps, inputs.performance_scores)

        # Prioritize or inject user-focused product if requested
        if config.focus_product_name:
            focus_lower = config.focus_product_name.strip().lower()
            matched = [o for o in candidate_opps if focus_lower in o.product_name.lower() or o.product_name.lower() in focus_lower]
            if matched:
                candidate_opps = matched + [o for o in candidate_opps if o not in matched]
            else:
                is_drink_foc = is_beverage_item(config.focus_product_name)
                forced_opp = OpportunityItem(
                    opportunity_id=f"opp_focus_{uuid.uuid4().hex[:6]}",
                    product_name=config.focus_product_name,
                    category="Minuman" if is_drink_foc else "Makanan",
                    signal="USER_FOCUSED_SELECTION",
                    score=99.0,
                    evidence={"selection": "Pilihan menu promosi spesifik dari pemilik usaha"},
                    unit_price=35000.0,
                    margin_pct=65.0,
                    estimated_lost_revenue=0.0,
                    status="DETECTED"
                )
                candidate_opps = [forced_opp] + candidate_opps

        for opp in candidate_opps:
            # Enforce max discount cap
            disc = min(config.default_discount_pct, max_discount)
            promo_p = round(opp.unit_price * (1.0 - (disc / 100.0)))

            # Enforce minimum gross margin guardrail:
            if opp.margin_pct is not None:
                cost = opp.unit_price * (1.0 - (opp.margin_pct / 100.0))
                post_discount_margin_pct = ((promo_p - cost) / promo_p) * 100.0 if promo_p > 0 else 0.0

                if post_discount_margin_pct < min_margin:
                    # Reject candidate promotion if resulting margin violates business rule
                    continue
                margin_summary = f"Dengan margin kotor pasca-diskon ({post_discount_margin_pct:.1f}% >= batas minimum {min_margin}%)"
            else:
                if min_margin > 25.0:
                    # Strict margin requirement cannot be verified without margin_pct data
                    continue
                margin_summary = "Data margin belum tersedia di POS (tidak diasumsikan)"

            opp_cat = (opp.category or "").lower()
            is_drink = is_beverage_item(opp.product_name, opp_cat)
            if is_drink:
                caption = (
                    f"☕ SEMANGAT HARI INI DENGAN {opp.product_name.upper()}!\n\n"
                    f"Lagi butuh mood booster buat nemenin aktivitasmu? Nikmati diskon spesial {int(disc)}% "
                    f"khusus menu favorit {opp.product_name}!\n\n"
                    f"🏷️ Harga Normal: Rp{int(opp.unit_price):,}\n"
                    f"🔥 Harga Promo: Rp{int(promo_p):,}\n\n"
                    f"Promo berlaku selama {config.promo_duration_days} hari ke depan di outlet atau pemesanan kasir. "
                    f"Yuk merapat sekarang sebelum kehabisan! ✨\n\n"
                    f"#PromoMinuman #NgopiSantai #{opp.product_name.replace(' ', '')} #KulinerIndonesia"
                )
            else:
                caption = (
                    f"🍽️ WAKTUNYA KULINER SPESIAL DENGAN {opp.product_name.upper()}!\n\n"
                    f"Lagi cari hidangan lezat dan memuaskan? Dapatkan penawaran promo hemat {int(disc)}% "
                    f"khusus menu favorit {opp.product_name}!\n\n"
                    f"🏷️ Harga Normal: Rp{int(opp.unit_price):,}\n"
                    f"🔥 Harga Promo: Rp{int(promo_p):,}\n\n"
                    f"Promo berlaku selama {config.promo_duration_days} hari ke depan di outlet atau kasir. "
                    f"Jangan lewatkan kesempatan makan enak hemat hari ini! ✨\n\n"
                    f"#PromoMakanan #MakanEnak #{opp.product_name.replace(' ', '')} #KulinerIndonesia"
                )

            plans.append(CampaignPlanItem(
                plan_id=f"plan_{uuid.uuid4().hex[:6]}",
                opportunity_id=opp.opportunity_id,
                product_name=opp.product_name,
                category=opp.category,
                headline=f"Promo Spesial {int(disc)}% — {opp.product_name}",
                strategy_rationale=(
                    f"Penjualan {opp.product_name} mengalami anomali {opp.signal} (turun {opp.evidence.get('drop_percentage', 0):.1f}%). "
                    f"{margin_summary}, "
                    f"diskon {int(disc)}% dirancang untuk mengaktifkan kembali daya beli target {config.target_audience} "
                    f"tanpa mengorbankan profitabilitas."
                ),
                target_audience=config.target_audience,
                original_price=opp.unit_price,
                discount_pct=disc,
                promo_price=float(promo_p),
                duration_days=config.promo_duration_days,
                caption=caption
            ))

            if len(plans) >= 2:
                break

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

    def apply_business_config(self, raw_config: Dict[str, Any], business_config: Dict[str, Any]) -> Dict[str, Any]:
        cfg = dict(raw_config)
        if "flyer_tagline" in business_config:
            cfg["tagline"] = str(business_config["flyer_tagline"])
        if "flyer_cta_text" in business_config:
            cfg["cta_text"] = str(business_config["flyer_cta_text"])
        if "flyer_style" in business_config:
            cfg["engine"] = str(business_config["flyer_style"])
        return cfg

    async def execute(self, config: GenerateFlyerConfig, inputs: GenerateFlyerInput, context: ExecutionContext) -> BatchFlyerAssets:
        if not inputs.campaign_plans:
            raise ValueError("No campaign plans provided to generate_flyer")

        assets: List[FlyerAssetItem] = []

        for idx, plan in enumerate(inputs.campaign_plans.plans):
            is_drink = is_beverage_item(plan.product_name, plan.category)
            default_outlet = "Kedai Kopi & Cafe" if is_drink else "Resto & Culinary Kitchen"
            outlet_name_val = default_outlet

            flyer_rel_url, engine_label = await FlyerGenerator.generate_dynamic_flyer(
                outlet_name=outlet_name_val,
                product_name=plan.product_name,
                category=plan.category,
                original_price=plan.original_price,
                discount_pct=plan.discount_pct,
                tagline=config.tagline,
                cta_text=config.cta_text,
                duration_days=plan.duration_days or 7,
                campaign_id=idx + 101,
                style=config.engine or "photography"
            )

            assets.append(FlyerAssetItem(
                asset_id=f"asset_{uuid.uuid4().hex[:6]}",
                plan_id=plan.plan_id,
                product_name=plan.product_name,
                flyer_url=flyer_rel_url,
                flyer_svg_content=flyer_rel_url,
                engine_used=engine_label,
                dimensions="1080x1080",
                discount_pct=plan.discount_pct or 0.0
            ))

        return BatchFlyerAssets(
            assets=assets,
            plans=inputs.campaign_plans.plans
        )


# =====================================================================
# =====================================================================
# AUTO-REGISTRATION HELPER
# =====================================================================

from app.core.governance_tools import register_governance_tools


def register_fnb_tools() -> None:
    """Registers all certified F&B marketing tools with ToolRegistry singleton."""
    register_governance_tools()
    tools = [
        IngestPosDataTool(),
        DetectOpportunitiesTool(),
        PlanCampaignTool(),
        GenerateFlyerTool(),
    ]
    for t in tools:
        ToolRegistry.register(t)
