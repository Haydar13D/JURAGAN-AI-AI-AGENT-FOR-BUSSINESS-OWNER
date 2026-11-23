"""
app/models/agent_run.py
Model persistensi untuk pending approvals (T18) dan agent run traces.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _default_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=24)


class PendingApproval(Base):
    """Menyimpan state eksekusi agent yang tertunda karena APPROVAL_REQUIRED (I16)."""

    __tablename__ = "pending_approvals"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    action_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    conversation_id: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)
    outlet_id: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    arguments_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    risk_tier: Mapped[str] = mapped_column(String(32), default="EXTERNAL", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="PENDING", index=True, nullable=False)

    messages_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    traces_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    pending_payload_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    steps_taken: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    tool_calls_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    reviewed_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_default_expiry,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
