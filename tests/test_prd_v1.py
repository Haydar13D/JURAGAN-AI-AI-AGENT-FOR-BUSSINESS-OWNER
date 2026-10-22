"""
tests/test_prd_v1.py
Unit tests untuk Functional Requirements PRD v1.0:
- FR-01 & FR-04: Opportunity Detector (SHARP_DROP)
- FR-07: Flyer Generator SVG Output
- FR-08 & FR-10: Approval Workflow & Idempotent Instagram Publishing
- FR-11: Closed-Loop Campaign Evaluator
"""

import os
import pytest
from datetime import date
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.services.opportunity_detector import OpportunityDetector
from app.services.flyer_generator import FlyerGenerator
from app.services.instagram_publisher import InstagramPublisherService
from app.services.campaign_evaluator import CampaignEvaluatorService
from app.models.campaign import Opportunity, Campaign

@pytest.mark.asyncio
async def test_opportunity_detector_sharp_drop():
    """Uji FR-04: Mendeteksi penurunan tajam Caramel Macchiato secara deterministik."""
    async with AsyncSessionLocal() as session:
        candidates = await OpportunityDetector.detect_opportunities(
            session=session,
            outlet_id=2,
            as_of_date=date(2026, 10, 20),
            cooldown_days=0
        )
        assert len(candidates) >= 1
        top = candidates[0]
        assert top["product_name"] == "Caramel Macchiato"
        assert top["signal"] == "SHARP_DROP"
        assert top["score"] >= 0.8
        assert "drop_qty_pct" in top["evidence"]
        assert top["evidence"]["drop_qty_pct"] >= 50.0

@pytest.mark.asyncio
async def test_flyer_generator_svg():
    """Uji FR-07: Flyer SVG ter-generate dengan format square 1080x1080 dan label simulasi."""
    svg_url = FlyerGenerator.generate_flyer_svg(
        outlet_name="Kopi Kenangan Senja",
        product_name="Caramel Macchiato",
        category="Kopi",
        original_price=32000,
        discount_pct=15.0,
        campaign_id=999
    )
    assert svg_url.startswith("/static/campaigns/")
    # Pastikan file ada di disk
    local_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "app", svg_url.lstrip("/")
    )
    assert os.path.exists(local_path)
    with open(local_path, "r", encoding="utf-8") as f:
        content = f.read()
        assert "DISKON 15%" in content
        assert "Caramel Macchiato" in content
        assert "JuraganAI Simulation Asset" in content

@pytest.mark.asyncio
async def test_instagram_publishing_and_idempotency():
    """Uji FR-10: Publikasi ke mock sandbox dan pencegahan posting ganda."""
    async with AsyncSessionLocal() as session:
        # Buat dummy campaign untuk testing
        camp = Campaign(
            outlet_id=2,
            product_name="Test Menu",
            objective="Test",
            brief="Test brief",
            caption="Test caption",
            status="APPROVED",
            version=1
        )
        session.add(camp)
        await session.commit()
        await session.refresh(camp)

        # 1. Publish pertama kali -> Berhasil
        res1 = await InstagramPublisherService.publish_campaign(session, campaign_id=camp.id)
        assert res1["status"] == "SUCCESS"
        assert "post_id" in res1
        assert res1["is_simulation"] is True

        # 2. Publish kedua kali -> Harus di-block oleh idempotency guardrail
        res2 = await InstagramPublisherService.publish_campaign(session, campaign_id=camp.id)
        assert res2["status"] == "ALREADY_PUBLISHED"

@pytest.mark.asyncio
async def test_campaign_evaluator():
    """Uji FR-11: Closed-loop evaluator menghasilkan metrik terukur."""
    async with AsyncSessionLocal() as session:
        stmt = select(Campaign).where(Campaign.outlet_id == 2)
        camps = (await session.execute(stmt)).scalars().all()
        if camps:
            eval_res = await CampaignEvaluatorService.evaluate_campaign(session, campaign_id=camps[0].id)
            assert "verdict" in eval_res
            assert "qty_growth_pct" in eval_res
            assert "narration" in eval_res
