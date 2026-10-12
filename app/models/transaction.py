from datetime import datetime
from typing import Optional, List
from sqlalchemy import String, Numeric, Integer, Text, ForeignKey, DateTime, Index, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base

class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("outlet_id", "invoice_number", name="uq_outlet_invoice"),
        Index("idx_transactions_time", "outlet_id", "transaction_time"),
        Index("idx_transactions_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    outlet_id: Mapped[int] = mapped_column(ForeignKey("outlets.id", ondelete="CASCADE"), nullable=False)
    invoice_number: Mapped[str] = mapped_column(String(100), nullable=False)
    transaction_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    subtotal: Mapped[float] = mapped_column(Numeric(14, 2), default=0.0, nullable=False)
    discount: Mapped[float] = mapped_column(Numeric(14, 2), default=0.0, nullable=False)
    tax: Mapped[float] = mapped_column(Numeric(14, 2), default=0.0, nullable=False)
    service_charge: Mapped[float] = mapped_column(Numeric(14, 2), default=0.0, nullable=False)
    total_amount: Mapped[float] = mapped_column(Numeric(14, 2), default=0.0, nullable=False)
    payment_method: Mapped[Optional[str]] = mapped_column(String(50), nullable=True) # QRIS, CASH, CARD, GO-PAY, OVO
    status: Mapped[str] = mapped_column(String(30), default="COMPLETED", nullable=False) # COMPLETED, VOID, REFUND
    cashier_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )

    outlet: Mapped["Outlet"] = relationship(back_populates="transactions")
    items: Mapped[List["TransactionItem"]] = relationship(back_populates="transaction", cascade="all, delete-orphan")

class TransactionItem(Base):
    __tablename__ = "transaction_items"
    __table_args__ = (
        Index("idx_items_trans_id", "transaction_id"),
        Index("idx_items_prod_name", "product_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    transaction_id: Mapped[int] = mapped_column(ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[Optional[int]] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), nullable=True)
    product_name: Mapped[str] = mapped_column(String(200), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    unit_price: Mapped[float] = mapped_column(Numeric(14, 2), default=0.0, nullable=False)
    discount: Mapped[float] = mapped_column(Numeric(14, 2), default=0.0, nullable=False)
    subtotal: Mapped[float] = mapped_column(Numeric(14, 2), default=0.0, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    transaction: Mapped["Transaction"] = relationship(back_populates="items")
    product: Mapped[Optional["Product"]] = relationship(back_populates="transaction_items")
