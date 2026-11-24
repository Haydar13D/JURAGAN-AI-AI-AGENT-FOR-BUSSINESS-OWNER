"""
app/services/memory_service.py
T20 — Memory bisnis persisten per outlet dengan allowlist key, anti-PII, batas 500 token,
dan tool memory.remember (INTERNAL).
"""

from datetime import datetime, timezone
import re
from typing import List, Optional, Sequence

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.core.tool_registry import (
    BaseTool,
    EmptyConfig,
    ExecutionContext,
    ToolCategory,
    ToolMetadata,
    ToolRegistry,
    ToolRiskTier,
)
from app.models.business_memory import BusinessFact

ALLOWED_BUSINESS_FACT_KEYS = frozenset({
    "menu_andalan",
    "margin_target_pct",
    "supplier_utama",
    "jam_ramai",
    "jam_sepi",
    "budget_promo_idr",
    "target_harian_idr",
})

MAX_MEMORY_CONTEXT_TOKENS = 500

_PHONE_RE = re.compile(r"(?:\+?62|08)[\d\-\s]{7,15}\d")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PII_PHRASE_RE = re.compile(
    r"\b(?:nama\s+pembeli|nama\s+pelanggan|no\.?\s*hp|nomor\s+hp|nomor\s+telepon|nik|ktp)\b",
    re.IGNORECASE,
)


def detect_customer_pii(value: str) -> Optional[str]:
    """Mendeteksi PII pelanggan (nomor HP, email, nama pembeli/NIK) agar ditolak."""
    if _PHONE_RE.search(value):
        return "terdeteksi pola nomor telepon/HP"
    if _EMAIL_RE.search(value):
        return "terdeteksi alamat email"
    if _PII_PHRASE_RE.search(value):
        return "terdeteksi penanda data pribadi pelanggan (nama pembeli/no HP/NIK)"
    return None


def estimate_tokens(text: str) -> int:
    """Estimasi konservatif jumlah token (1 token ~= 4 karakter)."""
    if not text:
        return 0
    return (len(text) + 3) // 4


class MemoryService:
    """Layanan CRUD dan injeksi konteks bisnis untuk agent."""

    @staticmethod
    def validate_key_and_value(key: str, value: str) -> None:
        clean_key = (key or "").strip()
        if clean_key not in ALLOWED_BUSINESS_FACT_KEYS:
            allowed = ", ".join(sorted(ALLOWED_BUSINESS_FACT_KEYS))
            raise ValueError(
                f"Key '{clean_key}' tidak didukung. Hanya boleh salah satu dari: {allowed}."
            )
        clean_val = (value or "").strip()
        if not clean_val:
            raise ValueError("Value fakta bisnis tidak boleh kosong.")
        pii_reason = detect_customer_pii(clean_val)
        if pii_reason:
            raise ValueError(f"Dilarang menyimpan PII pelanggan ({pii_reason}).")

    @classmethod
    async def upsert_fact(
        cls,
        session: AsyncSession,
        outlet_id: int,
        key: str,
        value: str,
        *,
        confidence: float = 1.0,
        source: str = "owner_chat",
    ) -> BusinessFact:
        cls.validate_key_and_value(key, value)
        clean_key = key.strip()
        clean_val = value.strip()

        stmt = (
            select(BusinessFact)
            .where(BusinessFact.outlet_id == outlet_id, BusinessFact.key == clean_key)
            .order_by(BusinessFact.updated_at.desc(), BusinessFact.id.desc())
        )
        existing = (await session.execute(stmt)).scalars().first()
        now_utc = datetime.now(timezone.utc)
        if existing is not None:
            existing.value = clean_val
            existing.confidence = float(confidence)
            existing.source = source
            existing.updated_at = now_utc
            fact = existing
        else:
            fact = BusinessFact(
                outlet_id=outlet_id,
                key=clean_key,
                value=clean_val,
                confidence=float(confidence),
                source=source,
                updated_at=now_utc,
            )
            session.add(fact)

        await session.commit()
        return fact

    @staticmethod
    async def get_facts(session: AsyncSession, outlet_id: int) -> List[BusinessFact]:
        stmt = (
            select(BusinessFact)
            .where(BusinessFact.outlet_id == outlet_id)
            .order_by(BusinessFact.updated_at.desc(), BusinessFact.id.desc())
        )
        return list((await session.execute(stmt)).scalars().all())

    @staticmethod
    def format_context_block(
        facts: Sequence[BusinessFact],
        max_tokens: int = MAX_MEMORY_CONTEXT_TOKENS,
    ) -> str:
        """Membentuk blok KONTEKS BISNIS <= max_tokens dengan prioritas updated_at terbaru."""
        if not facts:
            return ""
        header = "KONTEKS BISNIS:"
        lines: List[str] = [header]
        for fact in facts:
            candidate_line = f"- {fact.key}: {fact.value}"
            candidate_block = "\n".join([*lines, candidate_line])
            if estimate_tokens(candidate_block) > max_tokens:
                break
            lines.append(candidate_line)
        if len(lines) == 1:
            return ""
        return "\n".join(lines)


class RememberFactInput(BaseModel):
    key: str = Field(
        ...,
        description=(
            "Nama fakta bisnis dari allowlist: menu_andalan, margin_target_pct, "
            "supplier_utama, jam_ramai, jam_sepi, budget_promo_idr, target_harian_idr."
        ),
    )
    value: str = Field(..., min_length=1, description="Nilai fakta bisnis (tanpa PII pelanggan).")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Tingkat keyakinan fakta (0..1).")


class RememberFactOutput(BaseModel):
    saved: bool
    key: str
    value: str
    message: str


class RememberBusinessFactTool(BaseTool):
    metadata = ToolMetadata(
        tool_type="memory.remember",
        version="1.0.0",
        display_name="Remember Business Fact",
        description=(
            "Menyimpan fakta bisnis jangka panjang outlet ke database (bukan PII pelanggan). "
            "Gunakan saat owner menyebut preferensi/profil toko seperti menu andalan, target margin, "
            "supplier utama, jam ramai/sepi, budget promo, atau target omzet harian. "
            "Key wajib salah satu dari: menu_andalan, margin_target_pct, supplier_utama, "
            "jam_ramai, jam_sepi, budget_promo_idr, target_harian_idr. "
            "Mengembalikan status saved, key, value, dan pesan konfirmasi."
        ),
        category=ToolCategory.TRANSFORMATION,
        risk_tier=ToolRiskTier.INTERNAL,
        is_side_effect=True,
        requires_action_approval=False,
    )
    config_schema = EmptyConfig
    input_schema = RememberFactInput
    output_schema = RememberFactOutput

    async def execute(
        self,
        config: BaseModel,
        inputs: RememberFactInput,
        context: ExecutionContext,
    ) -> RememberFactOutput:
        outlet_id = int(context.extra_meta.get("outlet_id") or 1)
        db_session = context.extra_meta.get("db_session")
        if db_session is not None:
            fact = await MemoryService.upsert_fact(
                db_session,
                outlet_id=outlet_id,
                key=inputs.key,
                value=inputs.value,
                confidence=inputs.confidence,
                source="agent_tool",
            )
        else:
            async with AsyncSessionLocal() as session:
                fact = await MemoryService.upsert_fact(
                    session,
                    outlet_id=outlet_id,
                    key=inputs.key,
                    value=inputs.value,
                    confidence=inputs.confidence,
                    source="agent_tool",
                )
        return RememberFactOutput(
            saved=True,
            key=fact.key,
            value=fact.value,
            message=f"Fakta '{fact.key}' berhasil disimpan untuk outlet {outlet_id}.",
        )


def register_memory_tool() -> None:
    ToolRegistry.register(RememberBusinessFactTool())
