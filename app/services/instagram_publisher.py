"""
app/services/instagram_publisher.py
Instagram Publishing Service (PRD JuraganAI FR-10 & Sec. 4, 8)
- Memublikasikan kampanye yang telah disetujui secara eksplisit oleh owner
- Mendukung Sandbox / Mock Publisher dengan label simulasi yang jelas
- Idempotency guardrail: Mencegah publikasi ganda (double-posting)
- Menghasilkan audit event di AgentEvent
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.logging import logger
from app.core.tool_registry import (
    BaseTool,
    EmptyConfig,
    ExecutionContext,
    ToolCategory,
    ToolMetadata,
    ToolRegistry,
    ToolRiskTier,
)
from app.models.campaign import Campaign, CampaignContent, SocialPost, AgentEvent
from pathlib import Path
from app.services.asset_renderer import ImgBBStorageAdapter, StorageAdapter, prepare_instagram_asset
from app.services.instagram_api import InstagramApiError, InstagramGraphClient

MAX_PUBLISHES_PER_24H = 25

DEFAULT_FEED_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1080 1080" width="1080" height="1080">'
    '<rect x="0" y="0" width="1080" height="1080" fill="#1e293b"/>'
    '<text x="100" y="540" fill="#ffffff">JuraganAI Promo</text>'
    "</svg>"
)


class InstagramPublishError(ValueError):
    """Error terstruktur saat publish konten Instagram (mis. 404/409)."""

    def __init__(self, message: str, *, status_code: int = 400, error_code: str = "PUBLISH_ERROR"):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code

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

    @classmethod
    async def publish_content(
        cls,
        session: AsyncSession,
        content_id: str,
        idempotency_key: str,
        *,
        sandbox: Optional[bool] = None,
        api_client: Optional[InstagramGraphClient] = None,
        svg_source: Optional[str] = None,
        storage: Optional[StorageAdapter] = None,
    ) -> Dict[str, Any]:
        """Jalur tunggal publish Instagram dari CampaignContent berstatus APPROVED (T24, I21, I22)."""
        content = (
            await session.execute(
                select(CampaignContent).where(CampaignContent.content_id == content_id)
            )
        ).scalar_one_or_none()
        if content is None:
            raise InstagramPublishError(
                f"CampaignContent '{content_id}' tidak ditemukan.",
                status_code=404,
                error_code="NOT_FOUND",
            )

        # I22 Idempotensi: konten yang sudah tayang tidak boleh memanggil API kedua kali
        if content.status in {"PUBLISHED", "SANDBOX_PUBLISHED"} and content.post_id:
            return {
                "status": content.status,
                "content_id": content.content_id,
                "idempotency_key": idempotency_key,
                "post_id": content.post_id,
                "deduplicated": True,
                "is_sandbox": content.status == "SANDBOX_PUBLISHED",
                "message": "Konten sudah pernah tayang; hasil lama dikembalikan tanpa panggilan API kedua.",
            }

        # I21: hanya artefak berstatus APPROVED yang boleh tayang
        if content.status != "APPROVED":
            raise InstagramPublishError(
                f"Konten '{content_id}' berstatus '{content.status}', wajib APPROVED sebelum publish.",
                status_code=409,
                error_code="NOT_APPROVED",
            )

        # I22 Rate limit: maksimal 25 publish dalam 24 jam terakhir
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        recent_count = (
            await session.execute(
                select(func.count(CampaignContent.id)).where(
                    CampaignContent.status.in_(["PUBLISHED", "SANDBOX_PUBLISHED"]),
                    CampaignContent.created_at >= cutoff,
                )
            )
        ).scalar() or 0
        if recent_count >= MAX_PUBLISHES_PER_24H:
            return {
                "status": "RATE_LIMITED",
                "content_id": content_id,
                "idempotency_key": idempotency_key,
                "error_code": "RATE_LIMITED",
                "is_sandbox": False,
                "message": f"Batas {MAX_PUBLISHES_PER_24H} publish dalam 24 jam terakhir telah tercapai.",
            }

        is_sandbox = settings.INSTAGRAM_SANDBOX if sandbox is None else sandbox
        resolved_public_url: Optional[str] = None

        # Jika ada flyer JPEG yang sudah dirender di Campaign.media_url, upload ke ImgBB (jika IMGBB_API_KEY aktif)
        # atau gabungkan dengan ASSET_PUBLIC_BASE_URL.
        if not is_sandbox and svg_source is None and storage is None and content.campaign_id:
            camp = (
                await session.execute(select(Campaign).where(Campaign.id == content.campaign_id))
            ).scalar_one_or_none()
            if camp and camp.media_url and camp.media_url.startswith("/static/"):
                local_flyer = Path(__file__).resolve().parent.parent / camp.media_url.lstrip("/")
                if settings.IMGBB_API_KEY and local_flyer.exists():
                    imgbb = ImgBBStorageAdapter(settings.IMGBB_API_KEY)
                    resolved_public_url = await imgbb.upload_and_verify(local_flyer, local_flyer.name)
                elif settings.ASSET_PUBLIC_BASE_URL:
                    base = settings.ASSET_PUBLIC_BASE_URL.rstrip("/")
                    if base.endswith("/static/campaigns") and camp.media_url.startswith("/static/campaigns/"):
                        filename = camp.media_url.split("/static/campaigns/", 1)[1]
                        resolved_public_url = f"{base}/{filename}"
                    else:
                        resolved_public_url = f"{base}{camp.media_url}"

        effective_storage = storage
        if effective_storage is None and not is_sandbox and settings.IMGBB_API_KEY:
            effective_storage = ImgBBStorageAdapter(settings.IMGBB_API_KEY)

        if resolved_public_url is None:
            asset_res = await prepare_instagram_asset(
                svg_source or DEFAULT_FEED_SVG,
                f"{content_id}.svg",
                sandbox=is_sandbox,
                storage=effective_storage,
            )
        else:
            asset_res = None

        if is_sandbox:
            post_id = f"sandbox_ig_{uuid.uuid4().hex[:10]}"
            content.status = "SANDBOX_PUBLISHED"
            content.post_id = post_id
            await session.commit()
            return {
                "status": "SANDBOX_PUBLISHED",
                "content_id": content_id,
                "idempotency_key": idempotency_key,
                "post_id": post_id,
                "is_sandbox": True,
                "deduplicated": False,
                "message": "Mode INSTAGRAM_SANDBOX aktif: simulasi publish dicatat tanpa panggilan API Meta.",
            }

        final_image_url = resolved_public_url or (asset_res.public_url if asset_res else None)
        if (asset_res and asset_res.status == "STORAGE_NOT_CONFIGURED") or not final_image_url:
            return {
                "status": "STORAGE_NOT_CONFIGURED",
                "content_id": content_id,
                "idempotency_key": idempotency_key,
                "error_code": "STORAGE_NOT_CONFIGURED",
                "is_sandbox": False,
                "message": (asset_res.detail if asset_res else None) or "Storage publik (ASSET_PUBLIC_BASE_URL) belum dikonfigurasi untuk upload gambar Instagram.",
            }

        hashtags = json.loads(content.hashtags_json or "[]")
        full_caption = f"{content.caption}\n\n{' '.join(hashtags)}".strip()
        client = api_client or InstagramGraphClient()

        try:
            container_id = await client.create_media_container(final_image_url, full_caption)
            await client.wait_for_container_finished(container_id, timeout_seconds=60.0)
            post_id = await client.publish_media_container(container_id)
        except InstagramApiError as exc:
            logger.error(f"Instagram publish gagal untuk '{content_id}': {exc.actionable_message}")
            return {
                "status": exc.code.value,
                "content_id": content_id,
                "idempotency_key": idempotency_key,
                "error_code": exc.code.value,
                "is_sandbox": False,
                "message": exc.actionable_message,
            }

        content.status = "PUBLISHED"
        content.post_id = post_id
        await session.commit()
        return {
            "status": "PUBLISHED",
            "content_id": content_id,
            "idempotency_key": idempotency_key,
            "post_id": post_id,
            "public_url": asset_res.public_url,
            "is_sandbox": False,
            "deduplicated": False,
            "message": f"Konten '{content_id}' berhasil dipublikasikan ke Instagram (post_id={post_id}).",
        }


class InstagramPublishInput(BaseModel):
    """Input instagram.publish (I21): dilarang menerima caption/text/message bebas dari model."""

    content_id: str = Field(..., min_length=1, description="ID artefak CampaignContent berstatus APPROVED.")
    idempotency_key: str = Field(..., min_length=1, description="Kunci unik idempotensi agar publish tidak ganda.")


class InstagramPublishOutput(BaseModel):
    status: str
    content_id: str
    idempotency_key: str
    platform: str = "instagram"
    post_id: Optional[str] = None
    public_url: Optional[str] = None
    is_sandbox: bool = True
    deduplicated: bool = False
    error_code: Optional[str] = None
    message: str = ""


class InstagramPublishTool(BaseTool):
    metadata = ToolMetadata(
        tool_type="instagram.publish",
        version="1.0.0",
        display_name="Publish to Instagram",
        description=(
            "Memublikasikan artefak konten Instagram berstatus APPROVED ke akun Instagram bisnis. "
            "Hanya menerima content_id artefak yang sudah disetujui dan idempotency_key unik (I21). "
            "Menjaga batas 25 publish/24 jam dan idempoten per content_id."
        ),
        category=ToolCategory.EXPORT,
        risk_tier=ToolRiskTier.EXTERNAL,
        is_side_effect=True,
        requires_action_approval=True,
    )
    config_schema = EmptyConfig
    input_schema = InstagramPublishInput
    output_schema = InstagramPublishOutput

    async def execute(
        self,
        config: BaseModel,
        inputs: InstagramPublishInput,
        context: ExecutionContext,
    ) -> InstagramPublishOutput:
        if context.is_dry_run:
            return InstagramPublishOutput(
                status="SANDBOX_PUBLISHED",
                content_id=inputs.content_id,
                idempotency_key=inputs.idempotency_key,
                platform="instagram",
                post_id=f"dryrun_{inputs.content_id}",
                is_sandbox=True,
                message="Dry-run simulasi publish Instagram.",
            )
        db_session = context.extra_meta.get("db_session")
        if db_session is not None:
            res = await InstagramPublisherService.publish_content(
                db_session, inputs.content_id, inputs.idempotency_key
            )
        else:
            async with AsyncSessionLocal() as session:
                res = await InstagramPublisherService.publish_content(
                    session, inputs.content_id, inputs.idempotency_key
                )
        return InstagramPublishOutput(**res)


def register_instagram_tool() -> None:
    ToolRegistry.register(InstagramPublishTool())
