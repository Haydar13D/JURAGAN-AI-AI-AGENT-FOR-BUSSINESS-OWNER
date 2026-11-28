from typing import Optional
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.schemas.agent import (
    AgentChatRequest,
    AgentChatResponse,
    AgentRunTraceResponse,
    AgentStepSchema,
    ApprovalDecisionRequest,
    ConversationHistoryResponse,
    ConversationResetResponse
)
from app.services.agent_catalog import list_agent_profiles_summary
from app.services.agent_trace import get_agent_run_with_steps
from app.services.deepseek_agent import (
    ApprovalConflictError,
    ApprovalExpiredError,
    ApprovalNotFoundError,
    DeepSeekAgentService,
    LLMUnavailableError,
)

router = APIRouter(prefix="/agent", tags=["AI Agent Brain"])

@router.get("/profiles", summary="Daftar profil AI Agent spesialis beserta tool aktifnya")
async def get_agent_profiles():
    return list_agent_profiles_summary()


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
            conversation_id=payload.conversation_id,
            agent_id=payload.agent_id,
        )
        if response.status == "APPROVAL_REQUIRED":
            return JSONResponse(
                status_code=202,
                content=response.model_dump(),
            )
        return response
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
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


@router.get(
    "/runs/{run_id}",
    response_model=AgentRunTraceResponse,
    summary="Melihat Trace Eksekusi & Estimasi Biaya Satu Agent Run (Read-Only)"
)
async def get_agent_run_trace(
    run_id: str,
    db: AsyncSession = Depends(get_db),
):
    found = await get_agent_run_with_steps(db, run_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"AgentRun '{run_id}' tidak ditemukan.")
    run_row, step_rows = found
    return AgentRunTraceResponse(
        run_id=run_row.run_id,
        conversation_id=run_row.conversation_id,
        outlet_id=run_row.outlet_id,
        status=run_row.status,
        provider=run_row.provider,
        model=run_row.model,
        steps_taken=run_row.steps_taken,
        tool_calls_count=run_row.tool_calls_count,
        latency_ms=run_row.latency_ms,
        prompt_tokens=run_row.prompt_tokens,
        completion_tokens=run_row.completion_tokens,
        est_cost_idr=run_row.est_cost_idr,
        trigger=run_row.trigger,
        created_at=run_row.created_at.isoformat() if run_row.created_at else "",
        steps=[
            AgentStepSchema(
                step=s.step,
                tool_name=s.tool_name,
                arguments_json=s.arguments_json,
                output_digest=s.output_digest,
                error=s.error,
                duration_ms=s.duration_ms,
            )
            for s in step_rows
        ],
    )
