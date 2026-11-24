"""
tests/test_content_studio.py
T22 — Menguji ContentStudioService menghasilkan 3 varian DRAFT dan menolak hasil LLM yang ngarang angka.
"""

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select

from app.core.database import AsyncSessionLocal, init_db
from app.models.campaign import CampaignContent
from app.services.content_studio import ContentGroundingError, ContentStudioService


@pytest.mark.asyncio
async def test_content_studio_generates_three_draft_variants():
    await init_db()
    test_campaign_id = 9601
    payload = {
        "campaign_id": test_campaign_id,
        "product_name": "Kopi Susu Gula Aren",
        "discount_pct": 15,
        "promo_price_idr": 25000,
        "duration_days": 3,
    }

    fake_response = {
        "variants": [
            {
                "variant_index": 1,
                "style": "hard_sell",
                "caption": "Diskon 15% Kopi Susu Gula Aren jadi Rp25.000 selama 3 hari saja! ☕",
                "hashtags": ["#kopisusu", "#promo", "#diskon"],
                "cta": "Pesan sekarang!",
                "image_prompt": "Iced palm sugar coffee on wooden cafe table",
            },
            {
                "variant_index": 2,
                "style": "soft_relatable",
                "caption": "Kerja numpuk? Tenang, ada Kopi Susu Gula Aren Rp25.000 nemenin kamu.",
                "hashtags": ["#ngopipagi", "#kopisusu", "#wfc", "#kafe"],
                "cta": "Mampir yuk!",
                "image_prompt": "Warm aesthetic coffee shop morning light",
            },
            {
                "variant_index": 3,
                "style": "humor",
                "caption": "Dompet aman, hati senang: Kopi Susu Gula Aren turun harga 15% cuma 3 hari!",
                "hashtags": ["#kopihumor", "#promohemat", "#kopisusu"],
                "cta": "Serbu sebelum habis!",
                "image_prompt": "Playful illustrated coffee cup poster",
            },
        ]
    }

    class _FakeCompletions:
        async def create(self, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(fake_response)))]
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions()))

    async with AsyncSessionLocal() as session:
        await session.execute(delete(CampaignContent).where(CampaignContent.campaign_id == test_campaign_id))
        await session.commit()

        rows = await ContentStudioService.generate_variants(
            session,
            payload,
            campaign_id=test_campaign_id,
            known_catalog_products=["Kopi Susu Gula Aren", "Matcha Latte"],
            llm_client=fake_client,
            model="fake-model",
        )
        assert len(rows) == 3
        assert [r.style for r in rows] == ["hard_sell", "soft_relatable", "humor"]
        assert all(r.status == "DRAFT" for r in rows)

        persisted = list(
            (await session.execute(select(CampaignContent).where(CampaignContent.campaign_id == test_campaign_id)))
            .scalars()
            .all()
        )
        assert len(persisted) == 3


@pytest.mark.asyncio
async def test_content_studio_rejects_and_does_not_persist_ungrounded_llm_output():
    await init_db()
    test_campaign_id = 9602
    payload = {
        "campaign_id": test_campaign_id,
        "product_name": "Kopi Susu Gula Aren",
        "discount_pct": 15,
    }
    bad_response = {
        "variants": [
            {
                "variant_index": 1,
                "style": "hard_sell",
                "caption": "Promo diskon 50% Kopi Susu Gula Aren!",
                "hashtags": ["#kopi", "#promo", "#diskon"],
                "cta": "Beli sekarang!",
                "image_prompt": "Coffee poster",
            },
            {
                "variant_index": 2,
                "style": "soft_relatable",
                "caption": "Kopi Susu Gula Aren diskon 15%.",
                "hashtags": ["#kopi", "#promo", "#diskon"],
                "cta": "Yuk!",
                "image_prompt": "Coffee poster",
            },
            {
                "variant_index": 3,
                "style": "humor",
                "caption": "Kopi Susu Gula Aren bikin melek.",
                "hashtags": ["#kopi", "#promo", "#diskon"],
                "cta": "Gas!",
                "image_prompt": "Coffee poster",
            },
        ]
    }

    class _BadCompletions:
        async def create(self, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(bad_response)))]
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=_BadCompletions()))

    async with AsyncSessionLocal() as session:
        await session.execute(delete(CampaignContent).where(CampaignContent.campaign_id == test_campaign_id))
        await session.commit()

        with pytest.raises(ContentGroundingError, match="50"):
            await ContentStudioService.generate_variants(
                session,
                payload,
                campaign_id=test_campaign_id,
                llm_client=fake_client,
                model="fake-model",
            )

        persisted = list(
            (await session.execute(select(CampaignContent).where(CampaignContent.campaign_id == test_campaign_id)))
            .scalars()
            .all()
        )
        assert len(persisted) == 0
