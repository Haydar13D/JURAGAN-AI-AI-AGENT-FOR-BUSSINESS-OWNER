"""
tests/test_instagram_insights.py
T27 — Menguji feedback loop Instagram Insights (24h & 72h):
- Fake API -> metrik 24h dan 72h tersimpan idempoten di CampaignPerformance
- Permission kurang -> status INSIGHTS_UNAVAILABLE (bukan error)
- Data kosong / NULL -> tidak ada angka di ringkasan percakapan berikutnya
- Produk dengan performa konten bagus diprioritaskan di kampanye berikutnya
"""

from datetime import datetime, timedelta, timezone
import re

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.tool_registry import ExecutionContext
from app.models.base import Base
from app.models.campaign import Campaign, CampaignPerformance, Opportunity, SocialPost
from app.models.outlet import Outlet
from app.services.fnb_marketing.schemas import BatchOpportunities, OpportunityItem
from app.services.fnb_marketing.tools import PlanCampaignConfig, PlanCampaignInput, PlanCampaignTool
from app.services.instagram_api import InstagramApiError, InstagramErrorCode, InstagramGraphClient
from app.services.instagram_insights import (
    collect_due_post_insights,
    collect_post_insights,
    format_recent_insights_summary,
    get_top_performing_products,
)


async def _make_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    return engine, SessionLocal


@pytest.mark.asyncio
async def test_collect_24h_and_72h_insights_persists_metrics(monkeypatch):
    engine, SessionLocal = await _make_session()
    try:
        async def _fake_request(self, method, path, **kwargs):
            return {
                "data": [
                    {"name": "reach", "values": [{"value": 1200}]},
                    {"name": "saved", "values": [{"value": 43}]},
                    {"name": "profile_visits", "values": [{"value": 18}]},
                    {"name": "follows", "values": [{"value": 5}]},
                ]
            }

        monkeypatch.setattr(InstagramGraphClient, "_request", _fake_request)
        client = InstagramGraphClient(access_token="tok", ig_user_id="178414")

        async with SessionLocal() as session:
            outlet = Outlet(id=1, name="Kopi Test")
            opp = Opportunity(outlet_id=1, product_name="Kopi Susu", signal="SHARP_DROP", score=0.8, evidence_json="{}")
            session.add_all([outlet, opp])
            await session.flush()
            camp = Campaign(
                outlet_id=1,
                opportunity_id=opp.id,
                product_name="Kopi Susu",
                objective="Naikkan penjualan Kopi Susu",
                brief="Promo Kopi Susu",
                caption="Kopi Susu",
                discount_pct=10.0,
            )
            session.add(camp)
            await session.flush()
            pub_time = datetime.now(timezone.utc) - timedelta(hours=75)
            session.add(
                SocialPost(
                    campaign_id=camp.id,
                    platform="INSTAGRAM",
                    post_id="ig_post_100",
                    publish_status="PUBLISHED",
                    published_at=pub_time,
                )
            )
            await session.commit()

            rows = await collect_due_post_insights(session, graph_client=client)
            assert len(rows) == 2
            assert {r.window_hours for r in rows} == {24, 72}
            assert all(r.status == "COLLECTED" for r in rows)
            assert rows[0].reach == 1200
            assert rows[0].saved == 43
            assert rows[0].profile_visits == 18
            assert rows[0].follows == 5

            # Re-run must be idempotent (no new rows)
            rows_again = await collect_due_post_insights(session, graph_client=client)
            assert rows_again == []

            summary = await format_recent_insights_summary(session, outlet_id=1)
            assert "Kopi Susu" in summary
            assert "reach 1.2k" in summary
            assert "43 simpan" in summary
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_missing_insights_permission_sets_unavailable_without_error(monkeypatch):
    engine, SessionLocal = await _make_session()
    try:
        async def _fake_request(self, method, path, **kwargs):
            raise InstagramApiError(
                InstagramErrorCode.INSIGHTS_UNAVAILABLE,
                "INSIGHTS_UNAVAILABLE: Missing instagram_manage_insights permission.",
            )

        monkeypatch.setattr(InstagramGraphClient, "_request", _fake_request)
        client = InstagramGraphClient(access_token="tok", ig_user_id="178414")

        async with SessionLocal() as session:
            perf = await collect_post_insights(
                session,
                post_id="ig_post_noperm",
                window_hours=24,
                outlet_id=1,
                product_name="Matcha Latte",
                graph_client=client,
            )
            assert perf.status == "INSIGHTS_UNAVAILABLE"
            assert perf.reach is None
            assert perf.saved is None
            assert perf.profile_visits is None
            assert perf.follows is None

            summary = await format_recent_insights_summary(session, outlet_id=1)
            assert summary == ""
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_empty_or_null_insights_summary_contains_no_numbers():
    engine, SessionLocal = await _make_session()
    try:
        async with SessionLocal() as session:
            assert await format_recent_insights_summary(session, outlet_id=1) == ""

            # Row with COLLECTED status but all NULL metrics must not emit numbers
            session.add(
                CampaignPerformance(
                    outlet_id=1,
                    post_id="ig_post_nulls",
                    product_name="Teh Tarik",
                    window_hours=24,
                    status="COLLECTED",
                    reach=None,
                    saved=None,
                    profile_visits=None,
                    follows=None,
                )
            )
            await session.commit()
            summary = await format_recent_insights_summary(session, outlet_id=1)
            assert summary == ""
            assert re.search(r"\d", summary) is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_top_performing_product_prioritized_in_next_campaign():
    engine, SessionLocal = await _make_session()
    try:
        async with SessionLocal() as session:
            session.add_all([
                CampaignPerformance(
                    outlet_id=1,
                    post_id="ig_p1",
                    product_name="Americano",
                    window_hours=72,
                    status="COLLECTED",
                    reach=200,
                    saved=2,
                    profile_visits=1,
                    follows=0,
                ),
                CampaignPerformance(
                    outlet_id=1,
                    post_id="ig_p2",
                    product_name="Kopi Susu Gula Aren",
                    window_hours=72,
                    status="COLLECTED",
                    reach=1500,
                    saved=50,
                    profile_visits=25,
                    follows=8,
                ),
            ])
            await session.commit()

            scores = await get_top_performing_products(session, outlet_id=1)
            assert scores["Kopi Susu Gula Aren"] > scores["Americano"]

            tool = PlanCampaignTool()
            opps = BatchOpportunities(
                status="OK",
                detected_count=2,
                opportunities=[
                    OpportunityItem(
                        opportunity_id="opp_1",
                        product_name="Americano",
                        category="Minuman",
                        signal="SHARP_DROP",
                        score=0.9,
                        evidence={},
                        unit_price=25000.0,
                        margin_pct=65.0,
                    ),
                    OpportunityItem(
                        opportunity_id="opp_2",
                        product_name="Kopi Susu Gula Aren",
                        category="Minuman",
                        signal="SHARP_DROP",
                        score=0.5,
                        evidence={},
                        unit_price=28000.0,
                        margin_pct=65.0,
                    ),
                ],
            )
            ctx = ExecutionContext(tenant_id="1", workflow_id="wf_1", workflow_version=1, run_id="run_1")
            res = await tool.execute(
                PlanCampaignConfig(),
                PlanCampaignInput(opportunities=opps, performance_scores=scores),
                ctx,
            )
            assert res.plans[0].product_name == "Kopi Susu Gula Aren"
    finally:
        await engine.dispose()
