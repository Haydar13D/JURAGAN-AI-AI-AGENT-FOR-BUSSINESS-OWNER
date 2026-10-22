"""
app/api/v1/campaigns.py
API Endpoints untuk Siklus Otonom Agent, Approval, dan Publishing (PRD Section 10)
"""

import json
from datetime import datetime, date
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.logging import logger
from app.models.campaign import Opportunity, Campaign, CampaignApproval, AgentEvent, SocialPost
from app.services.opportunity_detector import OpportunityDetector
from app.services.campaign_planner import CampaignPlannerService
from app.services.instagram_publisher import InstagramPublisherService

router = APIRouter(tags=["Autonomous Agent & Campaigns"])

# --- Request & Response Schemas ---
class AgentRunRequest(BaseModel):
    outlet_id: int = 2
    as_of_date: Optional[str] = None  # Format YYYY-MM-DD

class ApprovalRequest(BaseModel):
    actor: str = "OWNER"
    decision: str = "APPROVED"  # APPROVED, REVISE, REJECTED
    feedback: Optional[str] = None
    target_version: int = 1

class RevisionRequest(BaseModel):
    actor: str = "OWNER"
    feedback: str

# --- Endpoints ---

@router.get("/opportunities")
async def list_opportunities(
    outlet_id: int = Query(2, description="ID Outlet"),
    db: AsyncSession = Depends(get_db)
):
    """Mengambil daftar peluang (opportunities) yang terdeteksi dari data penjualan."""
    stmt = select(Opportunity).where(
        Opportunity.outlet_id == outlet_id
    ).order_by(Opportunity.detected_at.desc())
    res = await db.execute(stmt)
    opps = res.scalars().all()
    
    result = []
    for o in opps:
        ev = json.loads(o.evidence_json) if isinstance(o.evidence_json, str) else o.evidence_json
        result.append({
            "id": o.id,
            "outlet_id": o.outlet_id,
            "product_name": o.product_name,
            "signal": o.signal,
            "score": o.score,
            "status": o.status,
            "detected_at": o.detected_at.strftime("%Y-%m-%d %H:%M:%S") if o.detected_at else "",
            "evidence": ev
        })
    return {"status": "SUCCESS", "total": len(result), "data": result}


@router.post("/agent-runs")
async def trigger_agent_run(
    req: AgentRunRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Memicu siklus otonom agent secara proaktif:
    1. Scan data penjualan (Opportunity Detector)
    2. Pilih 1 peluang prioritas tertinggi
    3. Rancang Campaign Proposal & Asset Flyer (LLM + SVG Renderer)
    4. Simpan status WAITING_APPROVAL
    """
    as_of = None
    if req.as_of_date:
        as_of = datetime.strptime(req.as_of_date, "%Y-%m-%d").date()

    candidates = await OpportunityDetector.detect_opportunities(
        session=db,
        outlet_id=req.outlet_id,
        as_of_date=as_of
    )

    if not candidates:
        return {
            "status": "NO_OPPORTUNITY",
            "message": "Tidak ada anomali atau peluang baru yang memenuhi kriteria cooldown saat ini.",
            "data": None
        }

    # Ambil 1 peluang dengan skor tertinggi
    top_cand = candidates[0]
    opp = await OpportunityDetector.create_opportunity_record(db, top_cand)

    # Rancang kampanye
    campaign = await CampaignPlannerService.plan_campaign_for_opportunity(db, opp.id)
    if not campaign:
        raise HTTPException(status_code=500, detail="Gagal merumuskan proposal kampanye.")

    return {
        "status": "CAMPAIGN_PROPOSED",
        "message": f"Agent berhasil mendeteksi peluang '{opp.signal}' pada '{opp.product_name}' dan menyusun proposal kampanye.",
        "opportunity_id": opp.id,
        "campaign_id": campaign.id,
        "campaign": {
            "id": campaign.id,
            "product_name": campaign.product_name,
            "objective": campaign.objective,
            "brief": campaign.brief,
            "caption": campaign.caption,
            "media_url": campaign.media_url,
            "discount_pct": campaign.discount_pct,
            "version": campaign.version,
            "status": campaign.status
        }
    }


@router.get("/campaigns")
async def list_campaigns(
    outlet_id: int = Query(2, description="ID Outlet"),
    db: AsyncSession = Depends(get_db)
):
    """Mengambil daftar seluruh kampanye promosi beserta statusnya."""
    stmt = select(Campaign).options(
        selectinload(Campaign.opportunity),
        selectinload(Campaign.social_posts)
    ).where(
        Campaign.outlet_id == outlet_id
    ).order_by(Campaign.created_at.desc())

    res = await db.execute(stmt)
    camps = res.scalars().all()

    data = []
    for c in camps:
        ev = None
        if c.opportunity and c.opportunity.evidence_json:
            ev = json.loads(c.opportunity.evidence_json) if isinstance(c.opportunity.evidence_json, str) else c.opportunity.evidence_json

        data.append({
            "id": c.id,
            "product_name": c.product_name,
            "objective": c.objective,
            "brief": c.brief,
            "caption": c.caption,
            "media_url": c.media_url,
            "discount_pct": c.discount_pct,
            "version": c.version,
            "status": c.status,
            "created_at": c.created_at.strftime("%Y-%m-%d %H:%M:%S") if c.created_at else "",
            "evidence": ev,
            "is_published": len(c.social_posts) > 0,
            "social_posts": [
                {
                    "platform": p.platform,
                    "post_id": p.post_id,
                    "url": p.post_url,
                    "status": p.publish_status,
                    "published_at": p.published_at.strftime("%Y-%m-%d %H:%M:%S") if p.published_at else ""
                }
                for p in c.social_posts
            ]
        })

    return {"status": "SUCCESS", "total": len(data), "data": data}


@router.get("/campaigns/{id}")
async def get_campaign_detail(
    id: int,
    db: AsyncSession = Depends(get_db)
):
    """Mengambil rincian proposal kampanye, bukti analisis, preview flyer, dan status approval."""
    stmt = select(Campaign).options(
        selectinload(Campaign.opportunity),
        selectinload(Campaign.approvals),
        selectinload(Campaign.social_posts)
    ).where(Campaign.id == id)

    res = await db.execute(stmt)
    c = res.scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Campaign tidak ditemukan.")

    ev = None
    if c.opportunity and c.opportunity.evidence_json:
        ev = json.loads(c.opportunity.evidence_json) if isinstance(c.opportunity.evidence_json, str) else c.opportunity.evidence_json

    return {
        "status": "SUCCESS",
        "data": {
            "id": c.id,
            "product_name": c.product_name,
            "objective": c.objective,
            "brief": c.brief,
            "caption": c.caption,
            "media_url": c.media_url,
            "discount_pct": c.discount_pct,
            "version": c.version,
            "status": c.status,
            "created_at": c.created_at.strftime("%Y-%m-%d %H:%M:%S") if c.created_at else "",
            "evidence": ev,
            "approvals": [
                {
                    "actor": a.actor,
                    "decision": a.decision,
                    "feedback": a.feedback,
                    "approved_version": a.approved_version,
                    "timestamp": a.timestamp.strftime("%Y-%m-%d %H:%M:%S") if a.timestamp else ""
                }
                for a in c.approvals
            ],
            "social_posts": [
                {
                    "platform": p.platform,
                    "post_id": p.post_id,
                    "post_url": p.post_url,
                    "status": p.publish_status,
                    "published_at": p.published_at.strftime("%Y-%m-%d %H:%M:%S") if p.published_at else ""
                }
                for p in c.social_posts
            ]
        }
    }


@router.post("/campaigns/{id}/approve")
async def approve_campaign(
    id: int,
    req: ApprovalRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Owner menyetujui proposal kampanye versi tertentu.
    Sistem mencatat approval dan otomatis memicu publikasi ke Instagram (atau mock).
    """
    stmt = select(Campaign).where(Campaign.id == id)
    res = await db.execute(stmt)
    c = res.scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Campaign tidak ditemukan.")

    if c.status in ["PUBLISHED", "COMPLETED"]:
        return {"status": "ALREADY_PUBLISHED", "message": "Campaign ini sudah dipublikasi."}

    # Catat approval
    approval = CampaignApproval(
        campaign_id=c.id,
        actor=req.actor,
        decision="APPROVED",
        feedback=req.feedback,
        approved_version=c.version
    )
    db.add(approval)
    c.status = "APPROVED"
    db.add(c)
    await db.commit()

    # Eksekusi publishing ke Instagram / Mock Sandbox
    publish_result = await InstagramPublisherService.publish_campaign(db, c.id, approved_by=req.actor)

    return {
        "status": "APPROVED_AND_PUBLISHED",
        "message": f"Proposal kampanye disetujui oleh {req.actor} dan berhasil diterbitkan ke Instagram.",
        "campaign_id": c.id,
        "publishing": publish_result
    }


@router.post("/campaigns/{id}/revise")
async def request_revision(
    id: int,
    req: RevisionRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Owner meminta revisi konten atau diskon.
    Status berubah menjadi REVISION_REQUESTED dan mencatat feedback owner.
    """
    stmt = select(Campaign).where(Campaign.id == id)
    res = await db.execute(stmt)
    c = res.scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Campaign tidak ditemukan.")

    approval = CampaignApproval(
        campaign_id=c.id,
        actor=req.actor,
        decision="REVISE",
        feedback=req.feedback,
        approved_version=c.version
    )
    db.add(approval)
    c.status = "REVISION_REQUESTED"
    db.add(c)

    # Catat AgentEvent
    event = AgentEvent(
        outlet_id=c.outlet_id,
        campaign_id=c.id,
        state="REVISION_REQUESTED",
        event_type="REVISION_REQUESTED",
        payload=json.dumps({"feedback": req.feedback, "version": c.version}, ensure_ascii=False)
    )
    db.add(event)
    await db.commit()

    return {
        "status": "REVISION_REQUESTED",
        "message": "Permintaan revisi berhasil dicatat. Konten versi baru wajib diapprove ulang.",
        "campaign_id": c.id,
        "feedback": req.feedback
    }


@router.post("/campaigns/{id}/reject")
async def reject_campaign(
    id: int,
    db: AsyncSession = Depends(get_db)
):
    """Owner menolak proposal kampanye."""
    stmt = select(Campaign).where(Campaign.id == id)
    res = await db.execute(stmt)
    c = res.scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Campaign tidak ditemukan.")

    c.status = "REJECTED"
    approval = CampaignApproval(
        campaign_id=c.id,
        actor="OWNER",
        decision="REJECTED",
        feedback="Ditolak oleh owner.",
        approved_version=c.version
    )
    db.add(approval)
    db.add(c)

    event = AgentEvent(
        outlet_id=c.outlet_id,
        campaign_id=c.id,
        state="REJECTED",
        event_type="CAMPAIGN_REJECTED",
        payload=json.dumps({"reason": "Owner menolak proposal kampanye."}, ensure_ascii=False)
    )
    db.add(event)
    await db.commit()

    return {
        "status": "REJECTED",
        "message": f"Proposal kampanye ID {c.id} telah ditolak.",
        "campaign_id": c.id
    }


@router.get("/agent-events")
async def list_agent_events(
    outlet_id: int = Query(2, description="ID Outlet"),
    db: AsyncSession = Depends(get_db)
):
    """Audit timeline aktivitas agent (PRD Section 10)."""
    stmt = select(AgentEvent).where(
        AgentEvent.outlet_id == outlet_id
    ).order_by(AgentEvent.created_at.desc()).limit(50)

    res = await db.execute(stmt)
    events = res.scalars().all()

    data = []
    for e in events:
        p_val = e.payload
        if p_val and isinstance(p_val, str):
            try:
                p_val = json.loads(p_val)
            except Exception:
                try:
                    import ast
                    p_val = ast.literal_eval(p_val)
                except Exception:
                    pass

        data.append({
            "id": e.id,
            "campaign_id": e.campaign_id,
            "state": e.state,
            "event_type": e.event_type,
            "created_at": e.created_at.strftime("%Y-%m-%d %H:%M:%S") if e.created_at else "",
            "payload": p_val
        })

    return {
        "status": "SUCCESS",
        "total": len(data),
        "data": data
    }


@router.get("/campaigns/{id}/evaluation")
async def evaluate_campaign_endpoint(
    id: int,
    db: AsyncSession = Depends(get_db)
):
    """Evaluasi performa kampanye penjualan sebelum vs sesudah (PRD FR-11)."""
    from app.services.campaign_evaluator import CampaignEvaluatorService
    try:
        eval_data = await CampaignEvaluatorService.evaluate_campaign(db, campaign_id=id)
        return {"status": "SUCCESS", "data": eval_data}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Gagal evaluasi campaign: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agent/dispatch-proposal-telegram")
async def dispatch_proposal_to_telegram(
    outlet_id: int = Query(2, description="ID Outlet"),
    chat_id: Optional[int] = Query(None, description="Chat ID Telegram penerima"),
    db: AsyncSession = Depends(get_db)
):
    """
    Memicu pengiriman materi proposal promosi interaktif ke Telegram owner secara instan (Demo & Uji Coba).
    """
    from app.core.config import settings
    from app.bot.telegram_bot import TelegramBotManager

    target_chat = chat_id or settings.TELEGRAM_DEFAULT_CHAT_ID
    if not target_chat:
        raise HTTPException(
            status_code=400,
            detail="Target Telegram Chat ID belum ditentukan dan TELEGRAM_DEFAULT_CHAT_ID belum ada di konfigurasi."
        )

    # 1. Cari campaign berstatus WAITING_APPROVAL
    stmt = select(Campaign).options(
        selectinload(Campaign.opportunity)
    ).where(
        Campaign.outlet_id == outlet_id,
        Campaign.status == "WAITING_APPROVAL"
    ).order_by(Campaign.created_at.desc())
    camp = (await db.execute(stmt)).scalars().first()

    # 2. Jika belum ada, jalankan deteksi dan buat proposal
    if not camp:
        candidates = await OpportunityDetector.detect_opportunities(db, outlet_id=outlet_id, cooldown_days=0)
        if not candidates:
            return {"status": "NO_OPPORTUNITY", "message": "Tidak ada anomali atau menu yang memerlukan promosi."}
        top_cand = candidates[0]
        opp = await OpportunityDetector.create_opportunity_record(db, top_cand)
        camp = await CampaignPlannerService.plan_campaign_for_opportunity(db, opp.id)

    # Ambil evidence
    ev = {}
    if camp.opportunity and camp.opportunity.evidence_json:
        ev = json.loads(camp.opportunity.evidence_json) if isinstance(camp.opportunity.evidence_json, str) else camp.opportunity.evidence_json

    # Kirim ke Telegram
    await TelegramBotManager.send_campaign_proposal(
        chat_id=target_chat,
        campaign=camp,
        evidence=ev
    )

    return {
        "status": "DISPATCHED",
        "message": f"Proposal kampanye #{camp.id} ({camp.product_name}) sukses dikirim ke Telegram chat {target_chat}!",
        "campaign_id": camp.id,
        "chat_id": target_chat
    }


