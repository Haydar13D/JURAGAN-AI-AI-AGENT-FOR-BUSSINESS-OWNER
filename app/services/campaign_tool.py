"""
app/services/campaign_tool.py
High-level tool `campaign.create_draft` untuk Chat Agent:
Menyimpan Campaign nyata ke DB, menghitung rekomendasi jam tayang dari peak hours,
dan (jika LLM aktif) menghasilkan 3 varian konten Content Studio (I20).
Tidak ada angka karangan (I3) dan tidak melakukan publish eksternal (risk_tier=INTERNAL).
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
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
from app.models.campaign import Campaign
from app.models.outlet import Outlet
from app.models.product import Product
from app.services.content_studio import ContentGroundingError, ContentStudioService
from app.services.flyer_generator import FlyerGenerator
from app.services.posting_scheduler import recommend_posting_slot


class CreateCampaignDraftInput(BaseModel):
    product_name: str = Field(..., min_length=1, max_length=200, description="Nama menu atau produk yang dipromosikan.")
    discount_pct: Optional[float] = Field(None, ge=0.0, le=50.0, description="Persentase diskon promo (0-50), opsional.")
    objective: str = Field(
        "Meningkatkan penjualan menu",
        min_length=1,
        max_length=255,
        description="Tujuan singkat kampanye promo.",
    )
    duration_days: int = Field(7, ge=1, le=30, description="Durasi promo dalam hari (1-30).")
    generate_content_variants: bool = Field(
        True,
        description="Jika true, hasilkan 3 varian konten Instagram (hard_sell, soft_relatable, humor) lewat Content Studio.",
    )
    generate_flyer: bool = Field(
        True,
        description="Jika true, render gambar poster flyer promosi 1080x1080 dan simpan ke media_url kampanye.",
    )

    @field_validator("product_name", "objective")
    @classmethod
    def _strip_non_empty(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("Field tidak boleh kosong.")
        return cleaned


class CampaignContentPreview(BaseModel):
    content_id: str
    variant_index: int
    style: str
    caption: str
    status: str


class CreateCampaignDraftOutput(BaseModel):
    status: str
    campaign_id: int
    product_name: str
    discount_pct: Optional[float] = None
    unit_price: Optional[float] = None
    media_url: Optional[str] = None
    recommended_slot_time: str
    recommended_slot_reason: str
    variants_status: str
    variants: List[CampaignContentPreview] = Field(default_factory=list)
    next_step: str


class CreateCampaignDraftTool(BaseTool):
    metadata = ToolMetadata(
        tool_type="campaign.create_draft",
        version="1.0.0",
        display_name="Buat Draf Kampanye & Konten",
        description=(
            "Membuat dan menyimpan draf kampanye promo nyata ke database, menghitung rekomendasi jam tayang "
            "dari peak hours kasir, serta menghasilkan 3 varian caption Instagram (Content Studio) untuk direview pemilik."
        ),
        category=ToolCategory.TRANSFORMATION,
        risk_tier=ToolRiskTier.INTERNAL,
        is_side_effect=False,
        requires_action_approval=False,
    )
    config_schema = EmptyConfig
    input_schema = CreateCampaignDraftInput
    output_schema = CreateCampaignDraftOutput

    async def execute(
        self,
        config: EmptyConfig,
        inputs: CreateCampaignDraftInput,
        context: ExecutionContext,
    ) -> CreateCampaignDraftOutput:
        session: Optional[AsyncSession] = context.extra_meta.get("db_session")
        outlet_id = int(context.extra_meta.get("outlet_id", 1))
        if session is None:
            raise RuntimeError("Database session tidak tersedia pada ExecutionContext.")

        # Ambil harga jual aktual produk dari DB jika tersedia (dilarang mengarang harga - I3)
        prod_row = (
            await session.execute(
                select(Product).where(
                    Product.outlet_id == outlet_id,
                    func.lower(Product.name) == inputs.product_name.lower(),
                )
            )
        ).scalar_one_or_none()
        actual_price: Optional[float] = (
            float(prod_row.base_price)
            if (prod_row and prod_row.base_price is not None and float(prod_row.base_price) > 0)
            else None
        )

        promo_note = f"Diskon {inputs.discount_pct:g}% " if inputs.discount_pct is not None else "Promo spesial "
        brief_text = f"{promo_note}untuk {inputs.product_name} selama {inputs.duration_days} hari. Tujuan: {inputs.objective}."
        initial_caption = f"{promo_note}{inputs.product_name} berlaku {inputs.duration_days} hari."

        campaign = Campaign(
            outlet_id=outlet_id,
            product_name=inputs.product_name,
            objective=inputs.objective,
            brief=brief_text,
            caption=initial_caption,
            discount_pct=inputs.discount_pct,
            status="WAITING_APPROVAL",
        )
        session.add(campaign)
        await session.commit()
        await session.refresh(campaign)

        flyer_url: Optional[str] = None
        if inputs.generate_flyer and not context.extra_meta.get("skip_flyer"):
            outlet_row = (
                await session.execute(select(Outlet).where(Outlet.id == outlet_id))
            ).scalar_one_or_none()
            outlet_name = outlet_row.name if outlet_row else "Outlet UMKM"
            try:
                flyer_url, _engine = await FlyerGenerator.generate_dynamic_flyer(
                    outlet_name=outlet_name,
                    product_name=inputs.product_name,
                    category="",
                    original_price=actual_price or 0.0,
                    discount_pct=inputs.discount_pct,
                    tagline=inputs.objective[:48],
                    cta_text="Klaim Promo di Kasir",
                    duration_days=inputs.duration_days,
                    campaign_id=campaign.id,
                )
                campaign.media_url = flyer_url
                await session.commit()
                await session.refresh(campaign)
            except Exception as exc:
                logger.error(f"Gagal generate flyer untuk campaign {campaign.id}: {exc}")

        today_str = datetime.now(ZoneInfo(settings.TIMEZONE)).date().isoformat()
        rec = await recommend_posting_slot(session, outlet_id, today_str)

        variants_out: List[CampaignContentPreview] = []
        variants_status = "SKIPPED"
        if inputs.generate_content_variants:
            payload: Dict[str, Any] = {
                "campaign_id": campaign.id,
                "product_name": inputs.product_name,
                "objective": inputs.objective,
                "duration_days": inputs.duration_days,
            }
            if inputs.discount_pct is not None:
                payload["discount_pct"] = inputs.discount_pct
            if actual_price is not None:
                payload["unit_price"] = actual_price
            try:
                rows = await ContentStudioService.generate_variants(
                    session,
                    payload,
                    campaign_id=campaign.id,
                    outlet_id=outlet_id,
                    llm_client=context.extra_meta.get("llm_client"),
                    model=context.extra_meta.get("llm_model"),
                )
                variants_out = [
                    CampaignContentPreview(
                        content_id=r.content_id,
                        variant_index=r.variant_index,
                        style=r.style,
                        caption=r.caption,
                        status=r.status,
                    )
                    for r in rows
                ]
                variants_status = "GENERATED"
            except (ContentGroundingError, Exception) as exc:
                logger.warning(f"Content Studio tidak dapat membuat varian untuk campaign {campaign.id}: {exc}")
                variants_status = "CONTENT_UNAVAILABLE"

        return CreateCampaignDraftOutput(
            status="DRAFT_CREATED",
            campaign_id=campaign.id,
            product_name=campaign.product_name,
            discount_pct=campaign.discount_pct,
            unit_price=actual_price,
            media_url=campaign.media_url,
            recommended_slot_time=rec.slot_time,
            recommended_slot_reason=rec.reason,
            variants_status=variants_status,
            variants=variants_out,
            next_step=(
                f"Draf kampanye #{campaign.id} tersimpan. Buka tab Kampanye untuk meninjau & menyetujui "
                f"varian konten sebelum dipublikasikan."
            ),
        )


def register_campaign_tool() -> None:
    if not ToolRegistry.is_allowlisted("campaign.create_draft"):
        ToolRegistry.register(CreateCampaignDraftTool())
