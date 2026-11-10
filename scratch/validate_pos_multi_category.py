import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import List, Dict, Any

from app.services.fnb_marketing.tools import (
    IngestPosDataTool, IngestPosDataConfig, IngestPosDataInput,
    DetectOpportunitiesTool, DetectOpportunitiesConfig, DetectOpportunitiesInput,
    PlanCampaignTool, PlanCampaignConfig, PlanCampaignInput,
    GenerateFlyerTool, GenerateFlyerConfig, GenerateFlyerInput
)
from app.core.tool_registry import ExecutionContext
from app.services.flyer_generator import FlyerGenerator

async def main():
    print("=" * 60)
    print("STEP 1: END-TO-END MULTI-CATEGORY VALIDATION")
    print("=" * 60)

    # 1. Test standard 60-day POS dataset
    pos_file = "data/synthetic_pos_60days.csv"
    with open(pos_file, "r", encoding="utf-8") as f:
        csv_text = f.read()

    ctx = ExecutionContext(tenant_id="tenant_umkm_01", run_id="run_step1_val", workflow_id="wf_fnb_prod", workflow_version=1)

    print(f"\n[1/4] Ingesting POS Data from {pos_file}...")
    ingest = IngestPosDataTool()
    pos_data = await ingest.execute(IngestPosDataConfig(), IngestPosDataInput(csv_content=csv_text), ctx)
    print(f"✓ Total transactions: {pos_data.total_transactions}")
    print(f"✓ Total unique products analyzed: {pos_data.analyzed_products_count}")
    for it in pos_data.items:
        print(f"   • {it.product_name:<30} | {it.category:<10} | Base: {it.baseline_qty:>4} | Curr: {it.current_qty:>4} | Change: {it.qty_change_pct:>6.1f}%")

    print("\n[2/4] Detecting Velocity Opportunities (SHARP_DROP)...")
    detect = DetectOpportunitiesTool()
    opps = await detect.execute(DetectOpportunitiesConfig(min_drop_percentage=15.0), DetectOpportunitiesInput(pos_data=pos_data), ctx)
    print(f"✓ Opportunities found: {len(opps.opportunities)}")
    for op in opps.opportunities:
        print(f"   • {op.product_name:<30} | {op.category:<10} | Score: {op.score:.2f} | Drop: {op.evidence.get('drop_percentage', 0):.1f}%")

    print("\n[3/4] Planning Campaign Copy & Strategy across Multiple Categories...")
    plan_tool = PlanCampaignTool()
    plans = await plan_tool.execute(
        PlanCampaignConfig(default_discount_pct=20.0, minimum_gross_margin_pct=25.0, promo_duration_days=7),
        PlanCampaignInput(opportunities=opps),
        ctx
    )
    print(f"✓ Campaign plans created: {len(plans.plans)}")
    for idx, p in enumerate(plans.plans):
        print(f"\n   --- Plan #{idx+1}: {p.product_name} ({p.category}) ---")
        print(f"   Headline: {p.headline}")
        print(f"   Original: Rp{int(p.original_price):,} -> Promo: Rp{int(p.promo_price):,} (Disc: {p.discount_pct}%)")
        is_food_copy = "#PromoMakanan" in p.caption
        is_drink_copy = "#PromoMinuman" in p.caption
        print(f"   Caption Classification: {'MAKANAN (#PromoMakanan)' if is_food_copy else ('MINUMAN (#PromoMinuman)' if is_drink_copy else 'OTHER')}")
        print(f"   Caption Sample:\n{p.caption[:180]}...")

    print("\n[4/4] Validating Prompt Construction for Multi-Category Menus...")
    test_cases = [
        ("Kopi Susu Gula Aren", "Kopi", "glassware / cup with latte art"),
        ("Matcha Latte", "Non-Kopi", "glassware / beverage"),
        ("Rice Bowl Ayam Sambal Matah", "Makanan", "artisan ceramic bowl"),
        ("Salmon Aburi Roll", "Makanan", "gourmet sushi on dark slate / geta board"),
        ("Double Beef Cheese Burger", "Makanan", "gourmet burger with melted cheese"),
        ("Almond Croissant", "Cemilan", "bakery pastry on elegant dessert plate"),
        ("Tenderloin Wagyu Steak", "Makanan", "succulent steak on skillet/platter")
    ]
    for prod, cat, expected_hint in test_cases:
        prompt = FlyerGenerator.build_image_prompt(prod, cat)
        print(f"\n   Product: {prod} ({cat})")
        print(f"   Expected Hint: {expected_hint}")
        print(f"   Generated Prompt: {prompt[:120]}...")
        # Verify no beverage words for food
        prod_l = prod.lower()
        if any(w in prod_l for w in ["sushi", "burger", "steak", "rice", "croissant"]):
            assert "glassware" not in prompt and "cafe table" not in prompt, f"Ambiguity detected: beverage words found in food prompt for {prod}"
        if any(w in prod_l for w in ["kopi", "matcha"]):
            assert "beverage" in prompt or "glassware" in prompt or "coffee cup" in prompt, f"Beverage context missing for {prod}"

    print("\n" + "=" * 60)
    print("ALL VALIDATION CRITERIA PASSED CLEANLY!")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(main())
