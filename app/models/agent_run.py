"""
app/models/agent_run.py
Model persistensi untuk pending approvals (T18) dan agent run traces.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _default_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=24)


class AgentRun(Base):
    """Mencatat setiap eksekusi chat/schedule/telegram agent (I17)."""

    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    conversation_id: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)
    outlet_id: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    steps_taken: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tool_calls_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    prompt_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    est_cost_idr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    trigger: Mapped[str] = mapped_column(String(32), default="chat", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class AgentStep(Base):
    """Mencatat setiap pemanggilan tool dalam satu AgentRun (I17)."""

    __tablename__ = "agent_steps"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("agent_runs.run_id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    step: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    arguments_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    output_digest: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


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


class WhatsappOutbox(Base):
    """Mencatat outbox & status pengiriman WhatsApp untuk idempotensi dan mode sandbox (T19)."""

    __tablename__ = "whatsapp_outbox"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    outlet_id: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    recipient: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)  # SANDBOX_QUEUED | SENT | NOT_CONFIGURED
    provider_message_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    response_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class ProactiveSlotRun(Base):
    """Menjamin idempotensi eksekusi scheduler proaktif per tanggal + slot + outlet (T21)."""

    __tablename__ = "proactive_slot_runs"
    __table_args__ = (
        UniqueConstraint("slot_date", "slot_label", "outlet_id", name="uq_proactive_slot"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    slot_date: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    slot_label: Mapped[str] = mapped_column(String(32), nullable=False)
    outlet_id: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)  # SENT | FAILED
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
