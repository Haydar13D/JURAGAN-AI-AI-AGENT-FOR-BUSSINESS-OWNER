"""
tests/test_fnb_multi_category_pos.py
Comprehensive End-to-End Multi-Category Validation Tests (Step 1).
Validates deterministic classification and presentation between food, sushi, steaks,
pastries, and beverages across the full Sales-to-Marketing pipeline.
"""

import pytest
import os

from app.core.tool_registry import ExecutionContext
from app.services.flyer_generator import FlyerGenerator, is_beverage_item
from app.services.fnb_marketing.tools import (
    IngestPosDataTool, IngestPosDataConfig, IngestPosDataInput,
    DetectOpportunitiesTool, DetectOpportunitiesConfig, DetectOpportunitiesInput,
    PlanCampaignTool, PlanCampaignConfig, PlanCampaignInput,
    GenerateFlyerTool, GenerateFlyerConfig, GenerateFlyerInput
)
from app.services.fnb_marketing.schemas import (
    BatchOpportunities, OpportunityItem
)
from app.services.fnb_marketing import register_fnb_tools


@pytest.fixture(autouse=True)
def setup_tools():
    register_fnb_tools()


@pytest.fixture
def exec_context() -> ExecutionContext:
    return ExecutionContext(
        tenant_id="tenant_umkm_test",
        run_id="run_multi_cat_test",
        workflow_id="wf_fnb_multi_cat",
        workflow_version=1
    )


class TestMultiCategoryDisambiguation:
    """Tests deterministic distinction between food and beverages across open-vocabulary items."""

    @pytest.mark.parametrize("food_name,category", [
        ("Tenderloin Wagyu Steak", "Makanan"),
        ("Sirloin Steak with Black Pepper", "Western"),
        ("Salmon Aburi Roll", "Sushi"),
        ("Spicy Tuna Maki", "Japanese"),
        ("Double Beef Burger", "Makanan"),
        ("Crispy Chicken Rice Bowl", "Makanan"),
        ("Almond Croissant", "Pastry"),
        ("Soto Ayam Lamongan", "Makanan"),
        ("Cireng Rujak Crispy", "Cemilan"),
        ("Kentang Goreng Fries", "Snack"),
    ])
    def test_food_items_not_classified_as_beverage(self, food_name: str, category: str):
        assert is_beverage_item(food_name, category) is False

    @pytest.mark.parametrize("drink_name,category", [
        ("Matcha Latte", "Non-Kopi"),
        ("Caramel Macchiato", "Kopi"),
        ("Kopi Susu Gula Aren", "Kopi"),
        ("Americano", "Kopi"),
        ("Es Teh Manis", "Minuman"),
        ("Lemonade Fresh", "Minuman"),
        ("Jus Alpukat", "Minuman"),
        ("Chocolate Milkshake", "Minuman"),
        ("Wedang Jahe", "Tradisional"),
    ])
    def test_beverage_items_classified_as_beverage(self, drink_name: str, category: str):
        assert is_beverage_item(drink_name, category) is True


class TestVisualPromptGeneration:
    """Tests visual prompt generation matches proper culinary/drink staging."""

    def test_sushi_roll_prompt(self):
        prompt = FlyerGenerator.build_image_prompt("Salmon Aburi Roll", "Makanan")
        assert "food photography" in prompt
        assert "sushi geta board" in prompt
        assert "glassware" not in prompt
        assert "beverage" not in prompt

    def test_steak_prompt(self):
        prompt = FlyerGenerator.build_image_prompt("Tenderloin Wagyu Steak", "Makanan")
        assert "food photography" in prompt
        assert "steak" in prompt
        assert "skillet" in prompt or "platter" in prompt
        assert "glassware" not in prompt
        assert "beverage" not in prompt

    def test_croissant_prompt(self):
        prompt = FlyerGenerator.build_image_prompt("Almond Croissant", "Cemilan")
        assert "food photography" in prompt
        assert "dessert plate" in prompt
        assert "glassware" not in prompt

    def test_burger_prompt(self):
        prompt = FlyerGenerator.build_image_prompt("Double Beef Burger", "Makanan")
        assert "food photography" in prompt
        assert "gourmet burger" in prompt
        assert "glassware" not in prompt

    def test_coffee_prompt(self):
        prompt = FlyerGenerator.build_image_prompt("Caramel Macchiato", "Kopi")
        assert "beverage photography" in prompt
        assert "coffee cup" in prompt

    def test_iced_beverage_prompt(self):
        prompt = FlyerGenerator.build_image_prompt("Es Teh Lemon", "Minuman")
        assert "beverage photography" in prompt
        assert "glassware" in prompt
        assert "ice cubes" in prompt


@pytest.mark.asyncio
class TestEndToEndPosPipeline:
    """Validates full pipeline execution on synthetic 60-day POS data."""

    async def test_full_pipeline_multi_category(self, exec_context):
        pos_path = "data/synthetic_pos_60days.csv"
        assert os.path.exists(pos_path)

        with open(pos_path, "r", encoding="utf-8") as f:
            csv_content = f.read()

        # Step 1: Ingestion
        ingest_tool = IngestPosDataTool()
        pos_summary = await ingest_tool.execute(
            IngestPosDataConfig(),
            IngestPosDataInput(csv_content=csv_content),
            exec_context
        )
        assert pos_summary.total_transactions > 0
        assert pos_summary.analyzed_products_count >= 5

        # Step 2: Detection
        detect_tool = DetectOpportunitiesTool()
        opps = await detect_tool.execute(
            DetectOpportunitiesConfig(min_drop_percentage=15.0),
            DetectOpportunitiesInput(pos_data=pos_summary),
            exec_context
        )
        assert len(opps.opportunities) >= 1

        # Step 3: Planning Copy & Guardrails
        plan_tool = PlanCampaignTool()
        plans = await plan_tool.execute(
            PlanCampaignConfig(default_discount_pct=20.0, minimum_gross_margin_pct=25.0, promo_duration_days=7),
            PlanCampaignInput(opportunities=opps),
            exec_context
        )
        assert len(plans.plans) >= 1

        for plan in plans.plans:
            is_drink = is_beverage_item(plan.product_name, plan.category)
            if is_drink:
                assert "#PromoMinuman" in plan.caption
                assert "☕ SEMANGAT HARI INI" in plan.caption
            else:
                assert "#PromoMakanan" in plan.caption
                assert "🍽️ WAKTUNYA KULINER SPESIAL" in plan.caption

    async def test_manual_forced_food_and_drink_planning(self, exec_context):
        """Tests that when both food and drink are planned, copy matches each category."""
        food_opp = OpportunityItem(
            opportunity_id="opp_steak_01",
            product_name="Tenderloin Wagyu Steak",
            category="Makanan",
            signal="MANUAL_TEST",
            score=90.0,
            evidence={"drop": 25.0},
            unit_price=85000.0,
            margin_pct=60.0,
            estimated_lost_revenue=1500000.0,
            status="DETECTED"
        )
        drink_opp = OpportunityItem(
            opportunity_id="opp_matcha_01",
            product_name="Matcha Latte",
            category="Non-Kopi",
            signal="MANUAL_TEST",
            score=85.0,
            evidence={"drop": 20.0},
            unit_price=28000.0,
            margin_pct=65.0,
            estimated_lost_revenue=500000.0,
            status="DETECTED"
        )
        opps = BatchOpportunities(detected_count=2, opportunities=[food_opp, drink_opp])

        plan_tool = PlanCampaignTool()
        plans = await plan_tool.execute(
            PlanCampaignConfig(default_discount_pct=20.0, minimum_gross_margin_pct=25.0, promo_duration_days=7),
            PlanCampaignInput(opportunities=opps),
            exec_context
        )
        assert len(plans.plans) == 2

        steak_plan = next(p for p in plans.plans if "Steak" in p.product_name)
        matcha_plan = next(p for p in plans.plans if "Matcha" in p.product_name)

        assert "#PromoMakanan" in steak_plan.caption
        assert "🍽️ WAKTUNYA KULINER SPESIAL" in steak_plan.caption
        assert "#PromoMinuman" not in steak_plan.caption

        assert "#PromoMinuman" in matcha_plan.caption
        assert "☕ SEMANGAT HARI INI" in matcha_plan.caption
        assert "#PromoMakanan" not in matcha_plan.caption
