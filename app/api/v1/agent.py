from typing import Optional
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.schemas.agent import (
    AgentChatRequest,
    AgentChatResponse,
    ApprovalDecisionRequest,
    ConversationHistoryResponse,
    ConversationResetResponse
)
from app.services.deepseek_agent import (
    ApprovalConflictError,
    ApprovalExpiredError,
    ApprovalNotFoundError,
    DeepSeekAgentService,
    LLMUnavailableError,
)

router = APIRouter(prefix="/agent", tags=["AI Agent Brain"])

@router.post("/chat", response_model=AgentChatResponse, summary="Percakapan AI Agent dengan Tool Calling & Memory")
async def chat_with_agent(
    payload: AgentChatRequest,
    db: AsyncSession = Depends(get_db)
):
    try:
        response = await DeepSeekAgentService.chat(
            session=db,
            user_message=payload.message,
            outlet_id=payload.outlet_id,
            conversation_id=payload.conversation_id
        )
        if response.status == "APPROVAL_REQUIRED":
            return JSONResponse(
                status_code=202,
                content=response.model_dump(),
            )
        return response
    except LLMUnavailableError as e:
        return JSONResponse(
            status_code=503,
            content={"code": "LLM_UNAVAILABLE", "detail": str(e)},
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Terjadi kesalahan pada AI Agent: {str(e)}"
        )

@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationHistoryResponse,
    summary="Melihat Riwayat Percakapan Sesi"
)
async def get_conversation_history(
    conversation_id: str,
    outlet_id: int = Query(1, description="ID Outlet"),
    db: AsyncSession = Depends(get_db)
):
    history = await DeepSeekAgentService.get_conversation_history(
        session=db,
        conversation_id=conversation_id,
        outlet_id=outlet_id
    )
    if not history:
        raise HTTPException(
            status_code=404,
            detail=f"Sesi percakapan '{conversation_id}' tidak ditemukan."
        )
    return history

@router.delete(
    "/conversations/{conversation_id}",
    response_model=ConversationResetResponse,
    summary="Reset Memori & Hapus Sesi Percakapan"
)
async def reset_conversation_session(
    conversation_id: str,
    outlet_id: int = Query(1, description="ID Outlet"),
    db: AsyncSession = Depends(get_db)
):
    success = await DeepSeekAgentService.reset_conversation(
        session=db,
        conversation_id=conversation_id,
        outlet_id=outlet_id
    )
    if not success:
        raise HTTPException(
            status_code=404,
            detail=f"Sesi percakapan '{conversation_id}' tidak ditemukan untuk direset."
        )
    return ConversationResetResponse(
        status="success",
        message=f"Sesi percakapan '{conversation_id}' berhasil direset.",
        conversation_id=conversation_id
    )


async def _handle_approval_decision(
    action_id: str,
    approved: bool,
    payload: Optional[ApprovalDecisionRequest],
    db: AsyncSession,
):
    reviewed_by = payload.reviewed_by if payload else "owner"
    try:
        response = await DeepSeekAgentService.resume_pending_approval(
            session=db,
            action_id=action_id,
            approved=approved,
            reviewed_by=reviewed_by,
        )
        if response.status == "APPROVAL_REQUIRED":
            return JSONResponse(status_code=202, content=response.model_dump())
        return response
    except ApprovalNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ApprovalConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ApprovalExpiredError as e:
        raise HTTPException(status_code=410, detail=str(e))
    except LLMUnavailableError as e:
        return JSONResponse(
            status_code=503,
            content={"code": "LLM_UNAVAILABLE", "detail": str(e)},
        )


@router.post(
    "/approvals/{action_id}/approve",
    response_model=AgentChatResponse,
    summary="Setujui Pending Approval dan Lanjutkan Eksekusi Agent"
)
async def approve_pending_action(
    action_id: str,
    payload: Optional[ApprovalDecisionRequest] = Body(default=None),
    db: AsyncSession = Depends(get_db),
):
    return await _handle_approval_decision(action_id=action_id, approved=True, payload=payload, db=db)


@router.post(
    "/approvals/{action_id}/reject",
    response_model=AgentChatResponse,
    summary="Tolak Pending Approval Tanpa Menjalankan Tool"
)
async def reject_pending_action(
    action_id: str,
    payload: Optional[ApprovalDecisionRequest] = Body(default=None),
    db: AsyncSession = Depends(get_db),
):
    return await _handle_approval_decision(action_id=action_id, approved=False, payload=payload, db=db)
