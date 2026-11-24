"""
tests/test_instagram_publish.py
T24 / I21 / I22 — Menguji jalur tunggal instagram.publish:
(a) Content berstatus DRAFT -> 409 (InstagramPublishError)
(b) Publish sukses (non-sandbox) -> status PUBLISHED, 1 post
(c) Publish ulang content_id sama -> deduplicated=True, 0 panggilan API kedua
(d) >= 25 publish dalam 24 jam -> ke-26 RATE_LIMITED, 0 panggilan API
(e) Sandbox -> 0 network, status SANDBOX_PUBLISHED (bukan PUBLISHED)
(f) Token invalid -> TOKEN_EXPIRED
(g) I21: input schema instagram.publish tidak punya caption/text/message
"""

from datetime import datetime, timezone
import json
import uuid

import pytest
from sqlalchemy import delete

from app.core.database import AsyncSessionLocal, init_db
from app.models.campaign import CampaignContent
from app.services.asset_renderer import AssetPrepareResult
from app.services.instagram_api import InstagramApiError, InstagramErrorCode
from app.services.instagram_publisher import (
    MAX_PUBLISHES_PER_24H,
    InstagramPublishError,
    InstagramPublishInput,
    InstagramPublisherService,
)


def _make_content(content_id: str, *, status: str = "DRAFT", outlet_id: int = 9501) -> CampaignContent:
    return CampaignContent(
        content_id=content_id,
        campaign_id=9501,
        outlet_id=outlet_id,
        variant_index=1,
        style="hard_sell",
        caption="Kopi Susu Gula Aren Rp25.000 diskon 15%.",
        hashtags_json=json.dumps(["#kopisusu", "#promo", "#umkm"]),
        cta="Pesan sekarang!",
        image_prompt="Coffee cup",
        status=status,
        created_at=datetime.now(timezone.utc),
    )


def test_instagram_publish_input_has_no_free_text_fields_i21():
    fields = set(InstagramPublishInput.model_fields.keys())
    assert "content_id" in fields
    assert "idempotency_key" in fields
    assert not (fields & {"caption", "text", "message"})


@pytest.mark.asyncio
async def test_draft_content_rejected_with_409():
    await init_db()
    cid = f"cnt_draft_{uuid.uuid4().hex[:8]}"
    async with AsyncSessionLocal() as session:
        session.add(_make_content(cid, status="DRAFT"))
        await session.commit()

        with pytest.raises(InstagramPublishError) as exc_info:
            await InstagramPublisherService.publish_content(session, cid, f"idem_{cid}", sandbox=True)
        assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_sandbox_publish_returns_sandbox_published_without_network():
    await init_db()
    cid = f"cnt_sbx_{uuid.uuid4().hex[:8]}"
    async with AsyncSessionLocal() as session:
        session.add(_make_content(cid, status="APPROVED"))
        await session.commit()

        res = await InstagramPublisherService.publish_content(session, cid, f"idem_{cid}", sandbox=True)
        assert res["status"] == "SANDBOX_PUBLISHED"
        assert res["is_sandbox"] is True
        assert res["post_id"].startswith("sandbox_ig_")


@pytest.mark.asyncio
async def test_live_publish_success_and_idempotent_second_call(monkeypatch, tmp_path):
    await init_db()
    cid = f"cnt_live_{uuid.uuid4().hex[:8]}"

    async def _fake_prepare(svg_source, filename, *, sandbox=None, output_dir=None, storage=None):
        return AssetPrepareResult(
            status="READY",
            local_path=str(tmp_path / "x.jpg"),
            public_url="https://cdn.example.test/campaigns/x.jpg",
            width=1080,
            height=1080,
            size_bytes=12345,
        )

    monkeypatch.setattr("app.services.instagram_publisher.prepare_instagram_asset", _fake_prepare)

    api_calls = []

    class _FakeGraphClient:
        async def create_media_container(self, image_url: str, caption: str) -> str:
            api_calls.append(("create", image_url))
            return "container_001"

        async def wait_for_container_finished(self, container_id: str, *, timeout_seconds: float = 60.0, poll_interval: float = 1.0) -> None:
            api_calls.append(("poll", container_id))

        async def publish_media_container(self, container_id: str) -> str:
            api_calls.append(("publish", container_id))
            return "ig_post_live_999"

    async with AsyncSessionLocal() as session:
        await session.execute(delete(CampaignContent).where(CampaignContent.outlet_id == 9501))
        session.add(_make_content(cid, status="APPROVED"))
        await session.commit()

        # (b) Publish pertama -> 1 post
        res1 = await InstagramPublisherService.publish_content(
            session, cid, f"idem_1_{cid}", sandbox=False, api_client=_FakeGraphClient()
        )
        assert res1["status"] == "PUBLISHED"
        assert res1["post_id"] == "ig_post_live_999"
        assert len(api_calls) == 3

        # (c) Publish ulang content yang sama -> 0 panggilan API kedua
        res2 = await InstagramPublisherService.publish_content(
            session, cid, f"idem_2_{cid}", sandbox=False, api_client=_FakeGraphClient()
        )
        assert res2["status"] == "PUBLISHED"
        assert res2["deduplicated"] is True
        assert res2["post_id"] == "ig_post_live_999"
        assert len(api_calls) == 3


@pytest.mark.asyncio
async def test_rate_limit_25_per_24h_blocks_26th_publish():
    await init_db()
    target_cid = f"cnt_26_{uuid.uuid4().hex[:8]}"
    async with AsyncSessionLocal() as session:
        await session.execute(delete(CampaignContent).where(CampaignContent.outlet_id == 9502))
        for i in range(MAX_PUBLISHES_PER_24H):
            row = _make_content(f"cnt_rl_{i}_{uuid.uuid4().hex[:6]}", status="PUBLISHED", outlet_id=9502)
            row.post_id = f"post_{i}"
            session.add(row)
        session.add(_make_content(target_cid, status="APPROVED", outlet_id=9502))
        await session.commit()

        res = await InstagramPublisherService.publish_content(
            session, target_cid, f"idem_{target_cid}", sandbox=True
        )
        assert res["status"] == "RATE_LIMITED"
        assert res["error_code"] == "RATE_LIMITED"


@pytest.mark.asyncio
async def test_invalid_token_returns_token_expired(monkeypatch, tmp_path):
    await init_db()
    cid = f"cnt_tok_{uuid.uuid4().hex[:8]}"

    async def _fake_prepare(svg_source, filename, *, sandbox=None, output_dir=None, storage=None):
        return AssetPrepareResult(
            status="READY",
            local_path=str(tmp_path / "x.jpg"),
            public_url="https://cdn.example.test/campaigns/x.jpg",
            width=1080,
            height=1080,
            size_bytes=12345,
        )

    monkeypatch.setattr("app.services.instagram_publisher.prepare_instagram_asset", _fake_prepare)

    class _ExpiredGraphClient:
        async def create_media_container(self, image_url: str, caption: str) -> str:
            raise InstagramApiError(InstagramErrorCode.TOKEN_EXPIRED, "TOKEN_EXPIRED: Token invalid")

    async with AsyncSessionLocal() as session:
        await session.execute(delete(CampaignContent).where(CampaignContent.outlet_id == 9502))
        session.add(_make_content(cid, status="APPROVED", outlet_id=9503))
        await session.commit()

        res = await InstagramPublisherService.publish_content(
            session, cid, f"idem_{cid}", sandbox=False, api_client=_ExpiredGraphClient()
        )
        assert res["status"] == "TOKEN_EXPIRED"
        assert res["error_code"] == "TOKEN_EXPIRED"
