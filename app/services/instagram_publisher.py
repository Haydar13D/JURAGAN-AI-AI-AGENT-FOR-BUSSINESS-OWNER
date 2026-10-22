"""
app/services/instagram_publisher.py
Instagram Publishing Service (PRD JuraganAI FR-10 & Sec. 4, 8)
- Memublikasikan kampanye yang telah disetujui secara eksplisit oleh owner
- Mendukung Sandbox / Mock Publisher dengan label simulasi yang jelas
- Idempotency guardrail: Mencegah publikasi ganda (double-posting)
- Menghasilkan audit event di AgentEvent
"""

import uuid
from datetime import datetime
from typing import Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.logging import logger
from app.models.campaign import Campaign, SocialPost, AgentEvent

class InstagramPublisherService:

    @classmethod
    async def publish_campaign(
        cls,
        session: AsyncSession,
        campaign_id: int,
        approved_by: str = "OWNER"
    ) -> Dict[str, Any]:
        """
        Mempublikasikan konten kampanye ke Instagram (atau Mock Publisher berlabel).
        HANYA dapat dieksekusi jika status campaign adalah APPROVED atau WAITING_APPROVAL dengan aksi approval langsung.
        """
        # 1. Ambil data campaign
        stmt = select(Campaign).where(Campaign.id == campaign_id)
        res = await session.execute(stmt)
        campaign = res.scalar_one_or_none()

        if not campaign:
            raise ValueError(f"Campaign ID {campaign_id} tidak ditemukan.")

        # Guardrail: Hanya publish jika status valid
        if campaign.status not in ["APPROVED", "WAITING_APPROVAL"]:
            if campaign.status in ["PUBLISHED", "MOCK_PUBLISHED", "EVALUATING", "COMPLETED"]:
                logger.warning(f"Campaign ID {campaign_id} sudah dipublikasi sebelumnya (idempotency triggered).")
                return {
                    "status": "ALREADY_PUBLISHED",
                    "campaign_id": campaign.id,
                    "message": "Konten kampanye ini sudah dipublikasikan sebelumnya."
                }
            raise ValueError(f"Tidak dapat mempublikasi: Campaign dalam status '{campaign.status}'. Wajib approval owner terlebih dahulu!")

        # 2. Cek apakah sudah ada SocialPost sebelumnya (Idempotency)
        post_stmt = select(SocialPost).where(
            SocialPost.campaign_id == campaign_id,
            SocialPost.platform == "INSTAGRAM"
        )
        existing_post = (await session.execute(post_stmt)).scalar_one_or_none()
        if existing_post:
            logger.warning(f"Social post sudah ada untuk Campaign ID {campaign_id}.")
            return {
                "status": "ALREADY_PUBLISHED",
                "campaign_id": campaign.id,
                "post_id": existing_post.post_id,
                "url": existing_post.post_url
            }

        # 3. Eksekusi Publishing (Mock Sandbox dengan label simulasi)
        mock_id = f"sim_ig_{uuid.uuid4().hex[:10]}"
        simulated_url = f"https://www.instagram.com/p/{mock_id}/"
        publish_time = datetime.now()

        social_post = SocialPost(
            campaign_id=campaign.id,
            platform="INSTAGRAM",
            platform_account_id="@kopi_kenangan_senja (Simulated)",
            post_id=mock_id,
            publish_status="MOCK_PUBLISHED",
            published_at=publish_time,
            post_url=simulated_url
        )
        session.add(social_post)

        # 4. Transisi Status Campaign
        campaign.status = "PUBLISHED"
        session.add(campaign)

        import json
        # 5. Catat Audit Trail Event
        audit_event = AgentEvent(
            outlet_id=campaign.outlet_id,
            campaign_id=campaign.id,
            state="PUBLISHED",
            event_type="POST_PUBLISHED",
            payload=json.dumps({
                "platform": "INSTAGRAM",
                "post_id": mock_id,
                "post_url": simulated_url,
                "approved_by": approved_by,
                "caption_length": len(campaign.caption),
                "is_simulation": True
            }, ensure_ascii=False)
        )
        session.add(audit_event)

        await session.commit()
        await session.refresh(campaign)

        logger.info(f"Campaign ID {campaign_id} berhasil dipublikasi ke Instagram (Mock ID: {mock_id}).")
        return {
            "status": "SUCCESS",
            "campaign_id": campaign.id,
            "post_id": mock_id,
            "post_url": simulated_url,
            "platform": "INSTAGRAM",
            "published_at": publish_time.strftime("%Y-%m-%d %H:%M:%S"),
            "is_simulation": True,
            "message": "Konten berhasil dipublikasikan ke akun Instagram uji (Label: Simulated Data)."
        }
