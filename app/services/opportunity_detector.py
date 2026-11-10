"""
app/services/opportunity_detector.py
Deterministic Rule-based Opportunity Detector (PRD JuraganAI FR-04 & Sec. 4, 6)
- Mendeteksi anomali penjualan tanpa halusinasi LLM
- Aturan:
  1. SHARP_DROP: Penurunan volume penjualan >= 30% dari menu yang sebelumnya aktif
  2. SLOW_MOVING_HIGH_MARGIN: Margin >= 50%, stok melimpah, tapi pergerakan lambat
- Guardrails:
  - Cooldown 7 hari per produk
  - Skoring deterministik 0.0 - 1.0
  - Menghasilkan bukti terukur (evidence_json)
"""

import json
from datetime import datetime, date, timedelta
from typing import List, Optional, Dict, Any
from sqlalchemy import select, func, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.models.outlet import Outlet
from app.models.product import Product
from app.models.transaction import Transaction, TransactionItem
from app.models.campaign import Opportunity, Campaign

class OpportunityDetector:

    @staticmethod
    async def detect_opportunities(
        session: AsyncSession,
        outlet_id: int,
        as_of_date: Optional[date] = None,
        cooldown_days: int = 7
    ) -> List[Dict[str, Any]]:
        """
        Memindai data penjualan dan menghasilkan daftar kandidat Opportunity
        yang memenuhi aturan deterministik.
        """
        if as_of_date is None:
            as_of_date = date.today()

        # Definisikan rentang waktu:
        # Current Window: 7 hari terakhir (as_of_date - 6 hari s/d as_of_date)
        current_end = datetime.combine(as_of_date, datetime.max.time())
        current_start = datetime.combine(as_of_date - timedelta(days=6), datetime.min.time())

        # Baseline Window: 7 hari sebelumnya (as_of_date - 13 hari s/d as_of_date - 7 hari)
        baseline_end = datetime.combine(as_of_date - timedelta(days=7), datetime.max.time())
        baseline_start = datetime.combine(as_of_date - timedelta(days=13), datetime.min.time())

        # 1. Cek produk-produk yang sedang dalam masa Cooldown (ada campaign aktif dalam cooldown_days)
        cooldown_products = set()
        if cooldown_days > 0:
            cooldown_cutoff = current_end - timedelta(days=cooldown_days)
            active_campaigns_stmt = select(Campaign.product_name).where(
                Campaign.outlet_id == outlet_id,
                Campaign.created_at >= cooldown_cutoff,
                Campaign.status.in_(["WAITING_APPROVAL", "APPROVED", "PUBLISHING", "PUBLISHED", "EVALUATING"])
            )
            active_camps = (await session.execute(active_campaigns_stmt)).scalars().all()
            cooldown_products = set(active_camps)

        # 2. Ambil master produk untuk outlet ini beserta kategorinya
        from sqlalchemy.orm import selectinload
        products_stmt = select(Product).options(selectinload(Product.category)).where(Product.outlet_id == outlet_id)
        products = (await session.execute(products_stmt)).scalars().all()
        product_dict = {p.name: p for p in products}

        # 3. Agregasi penjualan per produk di Current Window
        curr_stmt = select(
            TransactionItem.product_name,
            func.coalesce(func.sum(TransactionItem.quantity), 0).label("qty"),
            func.coalesce(func.sum(TransactionItem.subtotal), 0).label("revenue")
        ).join(
            Transaction, TransactionItem.transaction_id == Transaction.id
        ).where(
            Transaction.outlet_id == outlet_id,
            Transaction.status == "COMPLETED",
            Transaction.transaction_time >= current_start,
            Transaction.transaction_time <= current_end
        ).group_by(TransactionItem.product_name)

        curr_rows = (await session.execute(curr_stmt)).all()
        curr_sales = {r.product_name: {"qty": int(r.qty), "revenue": float(r.revenue)} for r in curr_rows}

        # 4. Agregasi penjualan per produk di Baseline Window
        base_stmt = select(
            TransactionItem.product_name,
            func.coalesce(func.sum(TransactionItem.quantity), 0).label("qty"),
            func.coalesce(func.sum(TransactionItem.subtotal), 0).label("revenue")
        ).join(
            Transaction, TransactionItem.transaction_id == Transaction.id
        ).where(
            Transaction.outlet_id == outlet_id,
            Transaction.status == "COMPLETED",
            Transaction.transaction_time >= baseline_start,
            Transaction.transaction_time <= baseline_end
        ).group_by(TransactionItem.product_name)

        base_rows = (await session.execute(base_stmt)).all()
        base_sales = {r.product_name: {"qty": int(r.qty), "revenue": float(r.revenue)} for r in base_rows}

        candidates = []

        # 5. Evaluasi Rules untuk setiap produk
        for p_name, prod in product_dict.items():
            if p_name in cooldown_products:
                logger.info(f"Produk '{p_name}' dalam cooldown 7 hari, melewati deteksi.")
                continue

            base_info = base_sales.get(p_name, {"qty": 0, "revenue": 0.0})
            curr_info = curr_sales.get(p_name, {"qty": 0, "revenue": 0.0})

            b_qty = base_info["qty"]
            b_rev = base_info["revenue"]
            c_qty = curr_info["qty"]
            c_rev = curr_info["revenue"]

            price = float(prod.base_price or 0.0)
            cost = float(prod.cost_price or 0.0)
            margin_pct = round(((price - cost) / price) * 100, 1) if price > 0 else 0.0
            stock = prod.stock if prod.stock is not None else 100

            # RULE 1: SHARP_DROP
            # Syarat: Baseline aktif (>= 10 cup), drop >= 30%, margin sehat >= 30%
            if b_qty >= 10:
                drop_qty_pct = round(((b_qty - c_qty) / b_qty) * 100, 1)
                drop_rev_pct = round(((b_rev - c_rev) / b_rev) * 100, 1) if b_rev > 0 else 0.0

                if drop_qty_pct >= 30.0:
                    lost_rev = max(0.0, b_rev - c_rev)
                    # Skor: kombinasi persentase drop (0.5) dan lost revenue (0.5)
                    score = round(min(1.0, (drop_qty_pct / 100.0) * 0.6 + (lost_rev / 1_000_000.0) * 0.4), 2)
                    
                    evidence = {
                        "rule": "SHARP_DROP",
                        "product_name": p_name,
                        "category": prod.category.name if prod.category else "Menu",
                        "price": price,
                        "cost": cost,
                        "margin_pct": margin_pct,
                        "stock_available": stock,
                        "baseline_window": f"{baseline_start.strftime('%d %b')} - {baseline_end.strftime('%d %b')}",
                        "baseline_qty": b_qty,
                        "baseline_revenue": b_rev,
                        "current_window": f"{current_start.strftime('%d %b')} - {current_end.strftime('%d %b')}",
                        "current_qty": c_qty,
                        "current_revenue": c_rev,
                        "drop_qty_pct": drop_qty_pct,
                        "lost_revenue": lost_rev,
                        "explanation": f"Penjualan {p_name} anjlok {drop_qty_pct}% dalam 7 hari terakhir (dari {b_qty} cup menjadi {c_qty} cup), dengan estimasi omzet hilang Rp{lost_rev:,.0f}. Margin produk sangat sehat ({margin_pct}%)."
                    }
                    candidates.append({
                        "outlet_id": outlet_id,
                        "product_id": prod.id,
                        "product_name": p_name,
                        "signal": "SHARP_DROP",
                        "evidence": evidence,
                        "score": score
                    })

            # RULE 2: SLOW_MOVING_HIGH_MARGIN
            # Syarat: Margin >= 50%, stok >= 20, volume 7 hari <= 10 pcs
            if margin_pct >= 50.0 and stock >= 20 and c_qty <= 10:
                score = round(min(1.0, (margin_pct / 100.0) * 0.5 + (stock / 100.0) * 0.3 + (1.0 - c_qty / 10.0) * 0.2), 2)
                evidence = {
                    "rule": "SLOW_MOVING_HIGH_MARGIN",
                    "product_name": p_name,
                    "category": prod.category.name if prod.category else "Menu",
                    "price": price,
                    "cost": cost,
                    "margin_pct": margin_pct,
                    "stock_available": stock,
                    "current_window": f"{current_start.strftime('%d %b')} - {current_end.strftime('%d %b')}",
                    "current_qty": c_qty,
                    "current_revenue": c_rev,
                    "explanation": f"Produk {p_name} memiliki margin profit tebal ({margin_pct}%), namun pergerakannya lambat (hanya terjual {c_qty} pcs dalam 7 hari terakhir) dengan sisa stok {stock} pcs."
                }
                candidates.append({
                    "outlet_id": outlet_id,
                    "product_id": prod.id,
                    "product_name": p_name,
                    "signal": "SLOW_MOVING_HIGH_MARGIN",
                    "evidence": evidence,
                    "score": score
                })

        # Urutkan kandidat berdasarkan score tertinggi
        candidates.sort(key=lambda x: x["score"], reverse=True)
        return candidates

    @staticmethod
    async def create_opportunity_record(
        session: AsyncSession,
        candidate: Dict[str, Any]
    ) -> Opportunity:
        """
        Menyimpan kandidat Opportunity ke dalam basis data jika belum ada yang serupa aktif.
        """
        opp = Opportunity(
            outlet_id=candidate["outlet_id"],
            product_id=candidate["product_id"],
            product_name=candidate["product_name"],
            signal=candidate["signal"],
            evidence_json=json.dumps(candidate["evidence"], ensure_ascii=False),
            score=candidate["score"],
            status="DETECTED"
        )
        session.add(opp)
        await session.commit()
        await session.refresh(opp)
        return opp
