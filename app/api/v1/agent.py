from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.schemas.agent import (
    AgentChatRequest,
    AgentChatResponse,
    ConversationHistoryResponse,
    ConversationResetResponse
)
from app.services.deepseek_agent import DeepSeekAgentService

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
        return response
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
