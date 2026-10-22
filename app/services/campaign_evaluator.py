"""
app/services/campaign_evaluator.py
Closed-Loop Campaign Evaluator (PRD JuraganAI FR-11 & Sec. 4, 6)
- Membandingkan performa penjualan produk sebelum vs sesudah periode promosi
- Mengikuti guardrail ketat: Menjelaskan korelasi, BUKAN klaim kausalitas mutlak
- Menghasilkan ringkasan evaluasi dan memperbarui status campaign
"""

import json
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.core.logging import logger
from app.models.campaign import Campaign, AgentEvent, SocialPost
from app.models.transaction import Transaction, TransactionItem

class CampaignEvaluatorService:

    @classmethod
    async def evaluate_campaign(
        cls,
        session: AsyncSession,
        campaign_id: int
    ) -> Dict[str, Any]:
        """
        Mengevaluasi dampak penjualan pasca-publikasi kampanye.
        """
        stmt = select(Campaign).where(Campaign.id == campaign_id)
        res = await session.execute(stmt)
        campaign = res.scalar_one_or_none()

        if not campaign:
            raise ValueError(f"Campaign ID {campaign_id} tidak ditemukan.")

        # Ambil waktu publikasi jika ada
        post_stmt = select(SocialPost).where(SocialPost.campaign_id == campaign_id)
        post = (await session.execute(post_stmt)).scalar_one_or_none()
        pub_time = post.published_at if post else campaign.created_at

        # Periode Evaluasi: 7 hari pasca publish vs 7 hari pra-publish
        eval_start = pub_time
        eval_end = pub_time + timedelta(days=7)

        # Baseline: 7 hari sebelum campaign
        baseline_start = pub_time - timedelta(days=7)
        baseline_end = pub_time

        # 1. Hitung penjualan selama follow-up period
        follow_stmt = select(
            func.coalesce(func.sum(TransactionItem.quantity), 0).label("qty"),
            func.coalesce(func.sum(TransactionItem.subtotal), 0).label("revenue")
        ).join(
            Transaction, TransactionItem.transaction_id == Transaction.id
        ).where(
            Transaction.outlet_id == campaign.outlet_id,
            Transaction.status == "COMPLETED",
            TransactionItem.product_name == campaign.product_name,
            Transaction.transaction_time >= eval_start,
            Transaction.transaction_time <= eval_end
        )
        f_row = (await session.execute(follow_stmt)).one()
        post_qty = int(f_row.qty)
        post_rev = float(f_row.revenue)

        # 2. Hitung penjualan baseline
        base_stmt = select(
            func.coalesce(func.sum(TransactionItem.quantity), 0).label("qty"),
            func.coalesce(func.sum(TransactionItem.subtotal), 0).label("revenue")
        ).join(
            Transaction, TransactionItem.transaction_id == Transaction.id
        ).where(
            Transaction.outlet_id == campaign.outlet_id,
            Transaction.status == "COMPLETED",
            TransactionItem.product_name == campaign.product_name,
            Transaction.transaction_time >= baseline_start,
            Transaction.transaction_time < baseline_end
        )
        b_row = (await session.execute(base_stmt)).one()
        pre_qty = int(b_row.qty)
        pre_rev = float(b_row.revenue)

        qty_delta_pct = round(((post_qty - pre_qty) / pre_qty) * 100, 1) if pre_qty > 0 else 0.0
        rev_delta_pct = round(((post_rev - pre_rev) / pre_rev) * 100, 1) if pre_rev > 0 else 0.0

        # Guardrail narasi PRD Sec. 6: Korelasi observasional, bukan klaim kausal mutlak
        if post_qty > pre_qty:
            verdict = "TERLIHAT_PENINGKATAN"
            narration = (
                f"Terdapat korelasi positif pada penjualan {campaign.product_name}: "
                f"volume meningkat {qty_delta_pct}% (dari {pre_qty} cup menjadi {post_qty} cup), "
                f"dengan kenaikan omzet Rp{post_rev - pre_rev:,.0f}. "
                f"(Catatan: Angka ini merefleksikan perbandingan periode observasi dan dipengaruhi faktor eksternal lainnya)."
            )
        elif post_qty < pre_qty:
            verdict = "BELUM_MENUNJUKKAN_HASIL"
            narration = (
                f"Penjualan {campaign.product_name} belum mengalami pemulihan: "
                f"volume tercatat {post_qty} cup (turun {abs(qty_delta_pct)}% dibanding baseline {pre_qty} cup). "
                f"Disarankan meninjau ulang penawaran bundling atau jam posting."
            )
        else:
            verdict = "PENJUALAN_STABIL"
            narration = f"Penjualan {campaign.product_name} stabil di angka {post_qty} cup selama periode observasi."

        eval_result = {
            "campaign_id": campaign.id,
            "product_name": campaign.product_name,
            "verdict": verdict,
            "baseline_window": f"{baseline_start.strftime('%d %b %Y')} - {baseline_end.strftime('%d %b %Y')}",
            "baseline_qty": pre_qty,
            "baseline_revenue": pre_rev,
            "evaluation_window": f"{eval_start.strftime('%d %b %Y')} - {eval_end.strftime('%d %b %Y')}",
            "evaluation_qty": post_qty,
            "evaluation_revenue": post_rev,
            "qty_growth_pct": qty_delta_pct,
            "revenue_growth_pct": rev_delta_pct,
            "narration": narration,
            "evaluated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

        # Update Campaign State ke COMPLETED jika sudah dievaluasi
        campaign.status = "COMPLETED"
        session.add(campaign)

        # Audit Event
        event = AgentEvent(
            outlet_id=campaign.outlet_id,
            campaign_id=campaign.id,
            state="COMPLETED",
            event_type="CAMPAIGN_EVALUATED",
            payload=json.dumps(eval_result, ensure_ascii=False)
        )
        session.add(event)
        await session.commit()

        return eval_result
