from datetime import datetime
from typing import Optional, List
from sqlalchemy import String, Text, BigInteger, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base

class Outlet(Base):
    __tablename__ = "outlets"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    telegram_chat_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )

    categories: Mapped[List["Category"]] = relationship(back_populates="outlet", cascade="all, delete-orphan")
    products: Mapped[List["Product"]] = relationship(back_populates="outlet", cascade="all, delete-orphan")
    transactions: Mapped[List["Transaction"]] = relationship(back_populates="outlet", cascade="all, delete-orphan")
    import_jobs: Mapped[List["ImportJob"]] = relationship(back_populates="outlet", cascade="all, delete-orphan")
