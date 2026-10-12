from datetime import datetime
from typing import Optional, List
from sqlalchemy import String, Numeric, ForeignKey, DateTime, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base

class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (
        UniqueConstraint("outlet_id", "name", name="uq_outlet_category"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    outlet_id: Mapped[int] = mapped_column(ForeignKey("outlets.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )

    outlet: Mapped["Outlet"] = relationship(back_populates="categories")
    products: Mapped[List["Product"]] = relationship(back_populates="category")

class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("outlet_id", "name", name="uq_outlet_product"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    outlet_id: Mapped[int] = mapped_column(ForeignKey("outlets.id", ondelete="CASCADE"), nullable=False)
    category_id: Mapped[Optional[int]] = mapped_column(ForeignKey("categories.id", ondelete="SET NULL"), nullable=True)
    sku: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    base_price: Mapped[float] = mapped_column(Numeric(14, 2), default=0.0, nullable=False)
    cost_price: Mapped[float] = mapped_column(Numeric(14, 2), default=0.0, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )

    outlet: Mapped["Outlet"] = relationship(back_populates="products")
    category: Mapped[Optional["Category"]] = relationship(back_populates="products")
    transaction_items: Mapped[List["TransactionItem"]] = relationship(back_populates="product")
