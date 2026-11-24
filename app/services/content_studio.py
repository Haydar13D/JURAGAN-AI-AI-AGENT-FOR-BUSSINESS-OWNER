"""
app/services/content_studio.py
T22 / I20 — Content Studio untuk menghasilkan 3 varian konten Instagram (hard_sell, soft_relatable, humor)
dengan validator grounding angka & produk terhadap payload kampanye.
"""

from datetime import datetime, timezone
import json
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set
import uuid

from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign import CampaignContent
from app.services.deepseek_agent import DeepSeekAgentService, LLMUnavailableError

REQUIRED_CONTENT_STYLES = ("hard_sell", "soft_relatable", "humor")
MAX_CAPTION_CHARS = 2200
MAX_EMOJI_COUNT = 3
MIN_HASHTAGS = 3
MAX_HASHTAGS = 5

_NUMBER_TOKEN_RE = re.compile(r"\d+(?:[.,]\d+)*")
_EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]")


class ContentGroundingError(ValueError):
    """Dilempar ketika konten melanggar aturan grounding (I20) atau batasan format."""
    pass


class ContentApprovalError(ValueError):
    """Dilempar ketika operasi approval/reject konten tidak valid (404/409)."""

    def __init__(self, message: str, *, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code


class ContentVariantDraft(BaseModel):
    variant_index: int = Field(..., ge=1, le=3)
    style: str
    caption: str
    hashtags: List[str] = Field(default_factory=list)
    cta: str = ""
    image_prompt: str = ""


def _normalize_number_token(token: str) -> str:
    """Menormalisasi angka format Indonesia (mis. 25.000 -> 25000, 15,5 -> 15.5)."""
    # Jika format ribuan titik (mis. 25.000 atau 1.250.000)
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", token):
        return token.replace(".", "")
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+", token):
        return token.replace(",", "")
    normalized = token.replace(",", ".")
    if "." in normalized:
        try:
            fval = float(normalized)
            if fval.is_integer():
                return str(int(fval))
            return str(fval)
        except ValueError:
            return normalized
    return normalized.lstrip("0") or "0"


def extract_numbers(text: str) -> List[str]:
    """Mengekstrak seluruh token angka yang ternormalisasi dari teks."""
    return [_normalize_number_token(m.group(0)) for m in _NUMBER_TOKEN_RE.finditer(text or "")]


def extract_allowed_numbers_from_payload(payload: Any) -> Set[str]:
    """Mengumpulkan semua angka yang ada di payload kampanye secara rekursif."""
    allowed: Set[str] = set()
    if isinstance(payload, bool) or payload is None:
        return allowed
    if isinstance(payload, int):
        allowed.add(str(payload))
        return allowed
    if isinstance(payload, float):
        if payload.is_integer():
            allowed.add(str(int(payload)))
        else:
            allowed.add(str(payload))
        return allowed
    if isinstance(payload, str):
        allowed.update(extract_numbers(payload))
        return allowed
    if isinstance(payload, dict):
        for v in payload.values():
            allowed.update(extract_allowed_numbers_from_payload(v))
        return allowed
    if isinstance(payload, (list, tuple, set)):
        for item in payload:
            allowed.update(extract_allowed_numbers_from_payload(item))
        return allowed
    return allowed


def extract_payload_products(payload: Dict[str, Any]) -> Set[str]:
    """Mengambil nama produk yang memang tercantum di payload kampanye."""
    names: Set[str] = set()

    def _walk(obj: Any) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in {"product_name", "menu_name", "item_name", "target_product", "product"} and isinstance(v, str):
                    if v.strip():
                        names.add(v.strip().lower())
                elif k in {"products", "items", "menu_items"} and isinstance(v, list):
                    for elem in v:
                        if isinstance(elem, str) and elem.strip():
                            names.add(elem.strip().lower())
                        else:
                            _walk(elem)
                else:
                    _walk(v)
        elif isinstance(obj, list):
            for elem in obj:
                _walk(elem)

    _walk(payload)
    return names


def count_emojis(text: str) -> int:
    return len(_EMOJI_RE.findall(text or ""))


def validate_content_variant(
    variant: ContentVariantDraft,
    campaign_payload: Dict[str, Any],
    known_catalog_products: Optional[Iterable[str]] = None,
) -> None:
    """Memvalidasi format dan grounding angka/produk satu varian konten (I20)."""
    if variant.style not in REQUIRED_CONTENT_STYLES:
        raise ContentGroundingError(f"Style '{variant.style}' tidak valid. Wajib: {REQUIRED_CONTENT_STYLES}.")
    if not variant.caption.strip():
        raise ContentGroundingError("Caption tidak boleh kosong.")
    if len(variant.caption) > MAX_CAPTION_CHARS:
        raise ContentGroundingError(
            f"Caption terlalu panjang ({len(variant.caption)} karakter; maksimal {MAX_CAPTION_CHARS})."
        )
    if count_emojis(variant.caption) > MAX_EMOJI_COUNT:
        raise ContentGroundingError(f"Terlalu banyak emoji di caption (maksimal {MAX_EMOJI_COUNT}).")
    if not (MIN_HASHTAGS <= len(variant.hashtags) <= MAX_HASHTAGS):
        raise ContentGroundingError(
            f"Jumlah hashtag wajib {MIN_HASHTAGS}..{MAX_HASHTAGS}, ditemukan {len(variant.hashtags)}."
        )

    allowed_numbers = extract_allowed_numbers_from_payload(campaign_payload)
    combined_text = f"{variant.caption}\n{variant.cta}"
    for num in extract_numbers(combined_text):
        if num not in allowed_numbers:
            raise ContentGroundingError(
                f"Angka '{num}' pada konten tidak ditemukan di payload kampanye (melanggar I20)."
            )

    if known_catalog_products:
        payload_products = extract_payload_products(campaign_payload)
        lowered_text = combined_text.lower()
        for prod in known_catalog_products:
            clean_prod = (prod or "").strip().lower()
            if clean_prod and clean_prod in lowered_text and clean_prod not in payload_products:
                raise ContentGroundingError(
                    f"Produk '{prod}' disebut di konten padahal tidak ada di payload kampanye."
                )


def _extract_json_payload(raw: str) -> Dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    return json.loads(text)


class ContentStudioService:
    """Menghasilkan dan memvalidasi 3 varian konten DRAFT dari payload kampanye."""

    @classmethod
    async def generate_variants(
        cls,
        session: AsyncSession,
        campaign_payload: Dict[str, Any],
        *,
        campaign_id: Optional[int] = None,
        outlet_id: int = 1,
        known_catalog_products: Optional[Iterable[str]] = None,
        llm_client: Optional[Any] = None,
        model: Optional[str] = None,
    ) -> List[CampaignContent]:
        client = llm_client
        model_name = model
        if client is None:
            providers = DeepSeekAgentService._get_active_providers()
            if not providers:
                raise LLMUnavailableError("Provider LLM tidak tersedia untuk Content Studio.")
            client = providers[0]["client"]
            model_name = model_name or providers[0]["model"]

        system_prompt = (
            "Anda adalah Content Studio JuraganAI untuk UMKM Indonesia.\n"
            "Buat TEPAT 3 varian konten Instagram (style: 'hard_sell', 'soft_relatable', 'humor') "
            "berdasarkan payload kampanye JSON.\n"
            "ATURAN KETAT:\n"
            "1. DILARANG mengarang angka (harga, diskon, persentase, durasi) yang tidak ada di payload.\n"
            "2. DILARANG menyebut nama produk yang tidak ada di payload.\n"
            "3. Caption Bahasa Indonesia, <= 2200 karakter, maksimal 3 emoji, 3-5 hashtag.\n"
            "4. Output WAJIB JSON valid dengan struktur:\n"
            '{"variants": [{"variant_index": 1, "style": "hard_sell", "caption": "...", '
            '"hashtags": ["#a", "#b", "#c"], "cta": "...", "image_prompt": "..."}, ...]}'
        )
        user_prompt = f"PAYLOAD KAMPANYE:\n{json.dumps(campaign_payload, ensure_ascii=False, default=str)}"

        resp = await client.chat.completions.create(
            model=model_name or "deepseek-chat",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.4,
        )
        raw_content = (resp.choices[0].message.content or "").strip()
        try:
            parsed = _extract_json_payload(raw_content)
        except Exception as exc:
            raise ContentGroundingError(f"Output LLM Content Studio bukan JSON valid: {exc}") from exc

        raw_variants = parsed.get("variants")
        if not isinstance(raw_variants, list) or len(raw_variants) != 3:
            raise ContentGroundingError("Content Studio wajib menghasilkan tepat 3 varian konten.")

        validated: List[ContentVariantDraft] = [ContentVariantDraft.model_validate(v) for v in raw_variants]
        styles_seen = {v.style for v in validated}
        if styles_seen != set(REQUIRED_CONTENT_STYLES):
            raise ContentGroundingError(
                f"Varian wajib mencakup {REQUIRED_CONTENT_STYLES}, ditemukan {sorted(styles_seen)}."
            )

        for variant in validated:
            validate_content_variant(
                variant,
                campaign_payload,
                known_catalog_products=known_catalog_products,
            )

        saved_rows: List[CampaignContent] = []
        resolved_campaign_id = campaign_id if campaign_id is not None else campaign_payload.get("campaign_id")
        for variant in validated:
            row = CampaignContent(
                content_id=f"cnt_{uuid.uuid4().hex[:12]}",
                campaign_id=resolved_campaign_id,
                outlet_id=outlet_id,
                variant_index=variant.variant_index,
                style=variant.style,
                caption=variant.caption,
                hashtags_json=json.dumps(variant.hashtags, ensure_ascii=False),
                cta=variant.cta,
                image_prompt=variant.image_prompt,
                status="DRAFT",
            )
            session.add(row)
            saved_rows.append(row)

        await session.commit()
        return saved_rows

    @classmethod
    async def approve_content(
        cls,
        session: AsyncSession,
        campaign_id: int,
        content_id: str,
        *,
        approved_by: str = "owner",
    ) -> CampaignContent:
        """Menyetujui satu varian konten dan menandai varian APPROVED lain di kampanye sama sebagai SUPERSEDED."""
        row = (
            await session.execute(
                select(CampaignContent).where(
                    CampaignContent.campaign_id == campaign_id,
                    CampaignContent.content_id == content_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise ContentApprovalError(
                f"Konten '{content_id}' untuk campaign {campaign_id} tidak ditemukan.",
                status_code=404,
            )
        if row.status == "REJECTED":
            raise ContentApprovalError(
                f"Konten '{content_id}' berstatus REJECTED dan tidak bisa di-approve ulang tanpa dibuat ulang.",
                status_code=409,
            )
        if row.status in {"PUBLISHED", "SANDBOX_PUBLISHED"}:
            raise ContentApprovalError(
                f"Konten '{content_id}' sudah dipublikasikan.",
                status_code=409,
            )

        await session.execute(
            update(CampaignContent)
            .where(
                CampaignContent.campaign_id == campaign_id,
                CampaignContent.content_id != content_id,
                CampaignContent.status == "APPROVED",
            )
            .values(status="SUPERSEDED")
        )

        row.status = "APPROVED"
        row.approved_by = approved_by
        row.approved_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(row)
        return row

    @classmethod
    async def reject_content(
        cls,
        session: AsyncSession,
        campaign_id: int,
        content_id: str,
        *,
        rejected_by: str = "owner",
    ) -> CampaignContent:
        """Menolak satu varian konten (status REJECTED)."""
        row = (
            await session.execute(
                select(CampaignContent).where(
                    CampaignContent.campaign_id == campaign_id,
                    CampaignContent.content_id == content_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise ContentApprovalError(
                f"Konten '{content_id}' untuk campaign {campaign_id} tidak ditemukan.",
                status_code=404,
            )
        if row.status in {"PUBLISHED", "SANDBOX_PUBLISHED"}:
            raise ContentApprovalError(
                f"Konten '{content_id}' sudah dipublikasikan sehingga tidak bisa ditolak.",
                status_code=409,
            )

        row.status = "REJECTED"
        row.approved_by = rejected_by
        row.approved_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(row)
        return row
