"""
tests/test_content_approval.py
T25 — Menguji approval varian konten:
- Approve A -> publish A sukses
- Approve B -> A otomatis SUPERSEDED -> publish A ditolak 409
- Konten REJECTED tidak bisa di-approve ulang (409)
- Konten tidak ditemukan -> 404
"""

import json
import uuid

from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import delete, select

from app.core.database import AsyncSessionLocal, init_db
from app.main import app
from app.models.campaign import CampaignContent
from app.services.instagram_publisher import InstagramPublishError, InstagramPublisherService


async def _seed_variants(campaign_id: int, count: int = 2) -> list[str]:
    await init_db()
    cids = []
    async with AsyncSessionLocal() as session:
        await session.execute(delete(CampaignContent).where(CampaignContent.campaign_id == campaign_id))
        for idx in range(1, count + 1):
            cid = f"cnt_t25_{campaign_id}_{idx}_{uuid.uuid4().hex[:6]}"
            session.add(
                CampaignContent(
                    content_id=cid,
                    campaign_id=campaign_id,
                    outlet_id=1,
                    variant_index=idx,
                    style="hard_sell" if idx == 1 else "soft_relatable",
                    caption=f"Varian {idx} Kopi Susu Rp25.000.",
                    hashtags_json=json.dumps(["#kopi", "#promo", "#umkm"]),
                    cta="Pesan sekarang!",
                    image_prompt="Coffee cup",
                    status="DRAFT",
                )
            )
            cids.append(cid)
        await session.commit()
    return cids


@pytest.mark.asyncio
async def test_approve_a_then_publish_a_succeeds():
    campaign_id = 9401
    cid_a, _ = await _seed_variants(campaign_id, count=2)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.post(
            f"/api/v1/campaigns/{campaign_id}/contents/{cid_a}/approve",
            json={"approved_by": "juragan_andi"},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "APPROVED"
        assert body["approved_by"] == "juragan_andi"
        assert body["approved_at"] is not None

    async with AsyncSessionLocal() as session:
        pub = await InstagramPublisherService.publish_content(session, cid_a, f"idem_{cid_a}", sandbox=True)
        assert pub["status"] == "SANDBOX_PUBLISHED"


@pytest.mark.asyncio
async def test_approve_b_supersedes_a_and_publish_a_returns_409():
    campaign_id = 9402
    cid_a, cid_b = await _seed_variants(campaign_id, count=2)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res_a = await ac.post(
            f"/api/v1/campaigns/{campaign_id}/contents/{cid_a}/approve",
            json={"approved_by": "owner"},
        )
        assert res_a.status_code == 200

        res_b = await ac.post(
            f"/api/v1/campaigns/{campaign_id}/contents/{cid_b}/approve",
            json={"approved_by": "owner"},
        )
        assert res_b.status_code == 200
        assert res_b.json()["status"] == "APPROVED"

    async with AsyncSessionLocal() as session:
        row_a = (
            await session.execute(select(CampaignContent).where(CampaignContent.content_id == cid_a))
        ).scalar_one()
        assert row_a.status == "SUPERSEDED"

        with pytest.raises(InstagramPublishError) as exc_info:
            await InstagramPublisherService.publish_content(session, cid_a, f"idem_{cid_a}", sandbox=True)
        assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_rejected_content_cannot_be_reapproved_and_missing_returns_404():
    campaign_id = 9403
    (cid_a,) = await _seed_variants(campaign_id, count=1)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        rej = await ac.post(
            f"/api/v1/campaigns/{campaign_id}/contents/{cid_a}/reject",
            json={"approved_by": "owner"},
        )
        assert rej.status_code == 200
        assert rej.json()["status"] == "REJECTED"

        re_app = await ac.post(
            f"/api/v1/campaigns/{campaign_id}/contents/{cid_a}/approve",
            json={"approved_by": "owner"},
        )
        assert re_app.status_code == 409

        not_found = await ac.post(
            f"/api/v1/campaigns/{campaign_id}/contents/cnt_missing/approve",
            json={"approved_by": "owner"},
        )
        assert not_found.status_code == 404
