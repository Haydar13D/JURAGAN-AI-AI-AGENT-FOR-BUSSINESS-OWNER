"""
app/services/agent_trace.py
T17 / I17 — Persistensi trace AgentRun + AgentStep serta estimasi biaya per percakapan.
"""

import json
from typing import Any, List, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.core.tool_registry import from_wire_name
from app.models.agent_run import AgentRun, AgentStep
from app.services.agent_loop import ToolCallTrace

MAX_TRACE_TEXT_CHARS = 2000

# Estimasi tarif wajar per token dalam IDR (~Rp 4.500/1M input token, ~Rp 18.000/1M output token)
IDR_PER_PROMPT_TOKEN = 0.0045
IDR_PER_COMPLETION_TOKEN = 0.018


def _truncate_2k(raw: Optional[str]) -> Optional[str]:
    if raw is None:
        return None
    if len(raw) <= MAX_TRACE_TEXT_CHARS:
        return raw
    return raw[:MAX_TRACE_TEXT_CHARS]


def estimate_cost_idr(
    prompt_tokens: Optional[int],
    completion_tokens: Optional[int],
) -> Optional[float]:
    """Mengembalikan estimasi biaya IDR jika token tersedia; None bila usage tidak ada."""
    if prompt_tokens is None and completion_tokens is None:
        return None
    p_cost = (prompt_tokens or 0) * IDR_PER_PROMPT_TOKEN
    c_cost = (completion_tokens or 0) * IDR_PER_COMPLETION_TOKEN
    return round(p_cost + c_cost, 4)


async def record_agent_run(
    session: AsyncSession,
    *,
    run_id: str,
    conversation_id: Optional[str],
    outlet_id: int,
    status: str,
    provider: Optional[str],
    model: Optional[str],
    steps_taken: int,
    tool_calls_count: int,
    latency_ms: int,
    prompt_tokens: Optional[int],
    completion_tokens: Optional[int],
    trigger: str,
    traces: Sequence[ToolCallTrace],
) -> AgentRun:
    """Menyimpan tepat satu AgentRun dan N AgentStep ke database."""
    est_cost = estimate_cost_idr(prompt_tokens, completion_tokens)
    run_row = AgentRun(
        run_id=run_id,
        conversation_id=conversation_id,
        outlet_id=outlet_id,
        status=status,
        provider=provider,
        model=model,
        steps_taken=steps_taken,
        tool_calls_count=tool_calls_count,
        latency_ms=latency_ms,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        est_cost_idr=est_cost,
        trigger=trigger,
    )
    session.add(run_row)

    for trace in traces:
        args_str = _truncate_2k(json.dumps(trace.arguments or {}, ensure_ascii=False, default=str)) or "{}"
        digest_str: Optional[str] = None
        if trace.output is not None:
            digest_str = _truncate_2k(json.dumps(trace.output, ensure_ascii=False, default=str))
        step_row = AgentStep(
            run_id=run_id,
            step=trace.step,
            tool_name=from_wire_name(trace.tool_name),
            arguments_json=args_str,
            output_digest=digest_str,
            error=_truncate_2k(trace.error),
            duration_ms=trace.duration_ms,
        )
        session.add(step_row)

    try:
        await session.commit()
    except Exception as exc:
        logger.error(f"Gagal menyimpan AgentRun '{run_id}': {exc}")
        await session.rollback()
        raise

    return run_row


async def get_agent_run_with_steps(
    session: AsyncSession,
    run_id: str,
) -> Optional[Tuple[AgentRun, List[AgentStep]]]:
    """Mengambil AgentRun beserta daftar AgentStep secara read-only."""
    stmt_run = select(AgentRun).where(AgentRun.run_id == run_id)
    run_row = (await session.execute(stmt_run)).scalar_one_or_none()
    if run_row is None:
        return None

    stmt_steps = (
        select(AgentStep)
        .where(AgentStep.run_id == run_id)
        .order_by(AgentStep.step.asc(), AgentStep.id.asc())
    )
    steps = list((await session.execute(stmt_steps)).scalars().all())
    return run_row, steps
