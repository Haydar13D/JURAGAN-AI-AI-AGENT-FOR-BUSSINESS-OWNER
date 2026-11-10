"""
app/api/v1/assistant.py
Conversational AI Business Assistant Endpoints (PRD Phase 2).
Enables natural language interaction for workflow execution, controlled configuration modification,
and business outcome explanations.
"""

from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.schemas.workflow import (
    AssistantMessageRequest,
    AssistantMessageResponse,
    BusinessProposal,
)
from app.services.business_config_service import BusinessConfigService
from app.core.database import AsyncSessionLocal
from app.core.logging import logger

router = APIRouter(prefix="/assistant", tags=["Kirata AI Business Assistant"])


class ProposalApprovalAction(BaseModel):
    workflow_id: str
    reviewer_id: str = "business_owner"
    reason: Optional[str] = None


@router.post("/message", response_model=AssistantMessageResponse, summary="Kirim pesan chat ke AI Business Assistant")
async def chat_with_assistant(req: AssistantMessageRequest) -> AssistantMessageResponse:
    """
    Main conversational endpoint:
    Processes natural language commands (RUN_WORKFLOW, MODIFY_WORKFLOW, EXPLAIN_RESULT, VIEW_WORKFLOW)
    and returns friendly Indonesian responses with structured proposals when relevant.
    """
    try:
        async with AsyncSessionLocal() as session:
            return await BusinessConfigService.handle_assistant_message(session, req)
    except Exception as exc:
        logger.error(f"Error handling assistant message: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Gagal memproses pesan assistant: {str(exc)}")


@router.get("/proposals/{proposal_id}", response_model=BusinessProposal, summary="Lihat detail proposal konfigurasi")
def get_proposal_detail(proposal_id: str) -> BusinessProposal:
    proposal = BusinessConfigService.get_proposal(proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail=f"Proposal '{proposal_id}' tidak ditemukan.")
    return proposal


@router.post("/proposals/{proposal_id}/approve", summary="Setujui proposal konfigurasi bisnis & naikkan versi alur kerja")
async def approve_proposal_endpoint(proposal_id: str, act: ProposalApprovalAction) -> Dict[str, Any]:
    try:
        async with AsyncSessionLocal() as session:
            new_wf, proposal = await BusinessConfigService.approve_proposal(
                session=session,
                workflow_id=act.workflow_id,
                proposal_id=proposal_id,
                reviewer_id=act.reviewer_id
            )
            try:
                from app.api.v1.workflows import _WORKFLOWS_STORE, _ACTIVE_VERSION
                if act.workflow_id not in _WORKFLOWS_STORE:
                    _WORKFLOWS_STORE[act.workflow_id] = {}
                _WORKFLOWS_STORE[act.workflow_id][new_wf.version] = new_wf
                _ACTIVE_VERSION[act.workflow_id] = new_wf.version
            except Exception:
                pass
            return {
                "status": "APPROVED",
                "message": f"Proposal '{proposal_id}' disetujui. Versi aktif alur kerja kini v{new_wf.version}.",
                "workflow": new_wf.model_dump(),
                "proposal": proposal.model_dump()
            }
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as exc:
        logger.error(f"Error approving proposal: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Gagal menyetujui proposal: {str(exc)}")


@router.post("/proposals/{proposal_id}/reject", summary="Tolak proposal konfigurasi bisnis")
def reject_proposal_endpoint(proposal_id: str, act: ProposalApprovalAction) -> Dict[str, Any]:
    try:
        proposal = BusinessConfigService.reject_proposal(
            proposal_id=proposal_id,
            reviewer_id=act.reviewer_id,
            reason=act.reason
        )
        return {
            "status": "REJECTED",
            "message": f"Proposal '{proposal_id}' ditolak. Versi alur kerja tidak berubah.",
            "proposal": proposal.model_dump()
        }
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))
