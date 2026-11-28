"""
tests/test_agent_catalog_and_campaign_tool.py
Menguji AgentCatalog (pemilihan agent spesialis + filter ToolRegistry) dan tool `campaign.create_draft` (I11).
"""

import json
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.tool_registry import ExecutionContext, ToolRegistry
from app.main import app
from app.models.base import Base
from app.models.campaign import Campaign, CampaignContent
from app.models.outlet import Outlet
from app.models.product import Product
from app.services.agent_catalog import (
    get_agent_profile,
    list_agent_profiles_summary,
    resolve_tools_for_profile,
)
from app.services.campaign_tool import (
    CreateCampaignDraftInput,
    CreateCampaignDraftTool,
    register_campaign_tool,
)


async def _make_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    return engine, SessionLocal


def test_agent_catalog_filters_tools_by_specialist_profile():
    register_campaign_tool()
    sales = get_agent_profile("sales_analyst")
    sales_types = {t.metadata.tool_type for t in resolve_tools_for_profile(sales)}
    assert "analytics.get_sales_overview" in sales_types
    assert "instagram.publish" not in sales_types
    assert "workflow.run" not in sales_types

    mkt = get_agent_profile("marketing_studio")
    mkt_types = {t.metadata.tool_type for t in resolve_tools_for_profile(mkt)}
    assert "campaign.create_draft" in mkt_types
    assert "instagram.publish" in mkt_types
    assert "workflow.run" not in mkt_types

    ops = get_agent_profile("ops_automation")
    ops_types = {t.metadata.tool_type for t in resolve_tools_for_profile(ops)}
    assert "workflow.run" in ops_types
    assert "whatsapp.send_message" in ops_types
    assert "campaign.create_draft" not in ops_types

    with pytest.raises(ValueError):
        get_agent_profile("unknown_agent_xyz")


@pytest.mark.asyncio
async def test_agent_profiles_endpoint_lists_all_profiles():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/api/v1/agent/profiles")
        assert res.status_code == 200
        ids = {item["agent_id"] for item in res.json()}
        assert {"general", "sales_analyst", "marketing_studio", "ops_automation"}.issubset(ids)


def test_campaign_create_draft_input_validation_empty_and_malformed():
    # Empty product_name rejected (I11)
    with pytest.raises(ValidationError):
        CreateCampaignDraftInput(product_name="   ")

    # Malformed discount_pct > 50 rejected (I11)
    with pytest.raises(ValidationError):
        CreateCampaignDraftInput(product_name="Kopi Susu", discount_pct=85.0)


@pytest.mark.asyncio
async def test_campaign_create_draft_tool_persists_campaign_and_variants_deterministically():
    engine, SessionLocal = await _make_session()
    try:
        fake_variants_payload = {
            "variants": [
                {
                    "variant_index": 1,
                    "style": "hard_sell",
                    "caption": "Promo Kopi Susu Gula Aren diskon 15% berlaku 7 hari! Pesan sekarang.",
                    "hashtags": ["#kopisusu", "#promo", "#ngopi"],
                    "cta": "Pesan sekarang",
                    "image_prompt": "Kopi susu gula aren segar",
                },
                {
                    "variant_index": 2,
                    "style": "soft_relatable",
                    "caption": "Butuh teman kerja sore? Kopi Susu Gula Aren lagi diskon 15% selama 7 hari.",
                    "hashtags": ["#kopisusu", "#santai", "#ngopi"],
                    "cta": "Mampir hari ini",
                    "image_prompt": "Suasana kafe sore",
                },
                {
                    "variant_index": 3,
                    "style": "humor",
                    "caption": "Dompet aman, hati senang: Kopi Susu Gula Aren diskon 15% untuk 7 hari!",
                    "hashtags": ["#kopisusu", "#hemat", "#ngopi"],
                    "cta": "Serbu sebelum habis",
                    "image_prompt": "Gelas kopi estetik",
                },
            ]
        }

        class _FakeCompletions:
            async def create(self, **kwargs):
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(fake_variants_payload)))]
                )

        fake_client = SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions()))

        async with SessionLocal() as session:
            session.add(Outlet(id=1, name="Kopi Demo"))
            session.add(Product(outlet_id=1, name="Kopi Susu Gula Aren", base_price=28000.0))
            await session.commit()

            tool = CreateCampaignDraftTool()
            ctx = ExecutionContext(
                tenant_id="outlet_1",
                workflow_id="agent_chat",
                workflow_version=1,
                run_id="run_test_1",
                extra_meta={
                    "db_session": session,
                    "outlet_id": 1,
                    "llm_client": fake_client,
                    "llm_model": "fake-model",
                    "skip_flyer": True,
                },
            )
            out = await tool.execute(
                tool.validate_config({}),
                CreateCampaignDraftInput(
                    product_name="Kopi Susu Gula Aren",
                    discount_pct=15.0,
                    duration_days=7,
                    generate_content_variants=True,
                ),
                ctx,
            )
            assert out.status == "DRAFT_CREATED"
            assert out.unit_price == 28000.0
            assert out.variants_status == "GENERATED"
            assert len(out.variants) == 3
            assert out.recommended_slot_time == "11:00"

            saved_camp = (await session.execute(select(Campaign).where(Campaign.id == out.campaign_id))).scalar_one()
            assert saved_camp.product_name == "Kopi Susu Gula Aren"
            assert saved_camp.status == "WAITING_APPROVAL"

            saved_contents = list(
                (await session.execute(select(CampaignContent).where(CampaignContent.campaign_id == out.campaign_id))).scalars().all()
            )
            assert len(saved_contents) == 3
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_cloudflare_image_generator_parses_base64_and_fallback_is_clean(monkeypatch):
    import base64
    import io
    from PIL import Image
    from app.services.flyer_generator import FlyerGenerator

    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (120, 80, 40)).save(buf, format="JPEG")
    fake_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    class _FakeResp:
        status_code = 200
        headers = {"content-type": "application/json"}
        content = b""
        text = ""

        def json(self):
            return {"success": True, "result": {"image": fake_b64}}

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, headers=None, json=None):
            assert "api.cloudflare.com/client/v4/accounts/acc_123/ai/run/" in url
            assert headers["Authorization"] == "Bearer tok_abc"
            return _FakeResp()

    monkeypatch.setattr("app.services.flyer_generator.httpx.AsyncClient", _FakeAsyncClient)
    img = await FlyerGenerator.generate_background_cloudflare(
        product_name="Es Serut",
        category="Minuman",
        account_id="acc_123",
        api_token="tok_abc",
    )
    assert img is not None
    assert img.size == (64, 64)

    fb = FlyerGenerator.get_clean_fallback_background("Es Serut", "Minuman")
    assert fb.size == (1080, 1080)
