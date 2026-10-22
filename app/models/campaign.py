from datetime import datetime
from typing import Optional, List
from sqlalchemy import String, Numeric, Integer, Text, ForeignKey, DateTime, Float, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base

class Opportunity(Base):
    __tablename__ = "opportunities"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    outlet_id: Mapped[int] = mapped_column(ForeignKey("outlets.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[Optional[int]] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), nullable=True)
    product_name: Mapped[str] = mapped_column(String(200), nullable=False)
    signal: Mapped[str] = mapped_column(String(50), nullable=False)  # SHARP_DROP, SLOW_MOVING_HIGH_MARGIN, POPULAR_SPIKE
    evidence_json: Mapped[str] = mapped_column(Text, nullable=False)  # JSON string berisi angka-angka komparasi
    score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)  # 0.0 s/d 1.0
    status: Mapped[str] = mapped_column(String(30), default="DETECTED", nullable=False)  # DETECTED, IN_PLANNING, CAMPAIGN_CREATED, DISMISSED, EXPIRED
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )

    campaigns: Mapped[List["Campaign"]] = relationship(back_populates="opportunity", cascade="all, delete-orphan")


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    outlet_id: Mapped[int] = mapped_column(ForeignKey("outlets.id", ondelete="CASCADE"), nullable=False)
    opportunity_id: Mapped[Optional[int]] = mapped_column(ForeignKey("opportunities.id", ondelete="SET NULL"), nullable=True)
    product_name: Mapped[str] = mapped_column(String(200), nullable=False)
    
    # Rencana & Konten Kampanye
    objective: Mapped[str] = mapped_column(String(255), nullable=False)
    brief: Mapped[str] = mapped_column(Text, nullable=False)
    caption: Mapped[str] = mapped_column(Text, nullable=False)
    media_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)  # URL flyer image/template
    discount_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(
        String(50),
        default="WAITING_APPROVAL",
        nullable=False
    )  # DETECTED, PLANNING, GENERATING, WAITING_APPROVAL, APPROVED, PUBLISHING, PUBLISHED, EVALUATING, COMPLETED, REVISION_REQUESTED, REJECTED, FAILED
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False
    )

    opportunity: Mapped[Optional["Opportunity"]] = relationship(back_populates="campaigns")
    approvals: Mapped[List["CampaignApproval"]] = relationship(back_populates="campaign", cascade="all, delete-orphan")
    social_posts: Mapped[List["SocialPost"]] = relationship(back_populates="campaign", cascade="all, delete-orphan")
    events: Mapped[List["AgentEvent"]] = relationship(back_populates="campaign", cascade="all, delete-orphan")


class CampaignApproval(Base):
    __tablename__ = "campaign_approvals"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False)
    actor: Mapped[str] = mapped_column(String(100), default="OWNER", nullable=False)
    decision: Mapped[str] = mapped_column(String(30), nullable=False)  # APPROVED, REVISE, REJECTED
    feedback: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    approved_version: Mapped[int] = mapped_column(Integer, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )

    campaign: Mapped["Campaign"] = relationship(back_populates="approvals")


class AgentEvent(Base):
    __tablename__ = "agent_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    outlet_id: Mapped[int] = mapped_column(ForeignKey("outlets.id", ondelete="CASCADE"), nullable=False)
    campaign_id: Mapped[Optional[int]] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=True)
    state: Mapped[str] = mapped_column(String(50), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g., OPPORTUNITY_DETECTED, CAMPAIGN_PROPOSED, OWNER_APPROVED, POST_PUBLISHED
    payload: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON metadata
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )

    campaign: Mapped[Optional["Campaign"]] = relationship(back_populates="events")


class SocialPost(Base):
    __tablename__ = "social_posts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False)
    platform: Mapped[str] = mapped_column(String(50), default="INSTAGRAM", nullable=False)
    platform_account_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    post_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # ID post dari Instagram / Mock ID
    publish_status: Mapped[str] = mapped_column(String(50), default="MOCK_PUBLISHED", nullable=False)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )
    post_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    campaign: Mapped["Campaign"] = relationship(back_populates="social_posts")
