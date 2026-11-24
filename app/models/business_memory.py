"""
app/models/business_memory.py
T20 — Tabel business_facts untuk menyimpan fakta bisnis jangka panjang per outlet.
"""

from datetime import datetime, timezone
from sqlalchemy import DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class BusinessFact(Base):
    """Fakta bisnis terkurasi per outlet (allowlist key, tanpa PII pelanggan)."""

    __tablename__ = "business_facts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    outlet_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    key: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    source: Mapped[str] = mapped_column(String(64), default="owner_chat", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )
