"""
app/services/agent_loop.py
Unified ReAct Agent Loop dengan guard eksplisit (I5).
Dipakai sebagai mesin eksekusi tunggal untuk chat, assistant, dan kanal lain.
"""

import asyncio
import json
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.core.tool_registry import BaseTool, ExecutionContext, ToolRiskTier, from_wire_name

DEFAULT_MAX_STEPS = 12
MAX_TOOL_CALLS_PER_STEP = 8
MAX_TOOL_OUTPUT_CHARS = 8000
DEFAULT_TOOL_TIMEOUT_SECONDS = 30.0
GATED_RISK_TIERS = {ToolRiskTier.EXTERNAL, ToolRiskTier.FINANCIAL}


class AgentStatus(str, Enum):
    DONE = "DONE"
    MAX_STEPS = "MAX_STEPS"
    TOOL_ERROR = "TOOL_ERROR"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    REJECTED = "REJECTED"
    LLM_UNAVAILABLE = "LLM_UNAVAILABLE"


class ToolCallTrace(BaseModel):
    step: int
    tool_name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    output: Any = None
    error: Optional[str] = None


class AgentResult(BaseModel):
    status: AgentStatus
    reply: str = ""
    steps_taken: int = 0
    tool_calls_count: int = 0
    traces: List[ToolCallTrace] = Field(default_factory=list)
    messages: List[Dict[str, Any]] = Field(default_factory=list)
    pending_approval: Optional[Dict[str, Any]] = None


def _truncate_output(raw: str, limit: int = MAX_TOOL_OUTPUT_CHARS) -> str:
    if len(raw) <= limit:
        return raw
    suffix = "... [truncated]"
    return raw[: max(0, limit - len(suffix))] + suffix


def _serialize_assistant_message(msg: Any) -> Dict[str, Any]:
    if isinstance(msg, dict):
        return dict(msg)
    payload: Dict[str, Any] = {
        "role": "assistant",
        "content": getattr(msg, "content", None) or "",
    }
    raw_calls = getattr(msg, "tool_calls", None)
    if raw_calls:
        serialized_calls = []
        for c in raw_calls:
            fn = getattr(c, "function", None)
            call_type = getattr(c, "type", None) or "function"
            serialized_calls.append({
                "id": getattr(c, "id", f"call_{uuid.uuid4().hex[:8]}"),
                "type": call_type,
                "function": {
                    "name": getattr(fn, "name", ""),
                    "arguments": getattr(fn, "arguments", "{}"),
                },
            })
        payload["tool_calls"] = serialized_calls
    return payload


def _build_tool_context(
    base_context: Optional[ExecutionContext],
    session: Optional[AsyncSession],
) -> ExecutionContext:
    if base_context is not None:
        meta = dict(base_context.extra_meta)
        if session is not None and "db_session" not in meta:
            meta["db_session"] = session
        return base_context.model_copy(update={"extra_meta": meta})
    meta: Dict[str, Any] = {}
    if session is not None:
        meta["db_session"] = session
    return ExecutionContext(
        tenant_id="default",
        workflow_id="agent_loop",
        workflow_version=1,
        run_id=f"run_{uuid.uuid4().hex[:8]}",
        actor_id="agent_loop",
        extra_meta=meta,
    )


def _serialize_call(call: Any) -> Dict[str, Any]:
    if isinstance(call, dict):
        return dict(call)
    fn = getattr(call, "function", None)
    return {
        "id": getattr(call, "id", f"call_{uuid.uuid4().hex[:8]}"),
        "name": getattr(fn, "name", ""),
        "arguments": getattr(fn, "arguments", "{}") or "{}",
    }


def _resolve_tool(tool_map: Dict[str, BaseTool], fn_name: str) -> Optional[BaseTool]:
    """Mencocokkan wire name dari LLM ke tool di tool_map melalui decode."""
    return tool_map.get(from_wire_name(fn_name))


def _needs_approval(tool: Optional[BaseTool], exec_ctx: ExecutionContext) -> bool:
    if tool is None:
        return False
    return (
        tool.metadata.risk_tier in GATED_RISK_TIERS
        and exec_ctx.extra_meta.get("action_approved") is not True
    )


async def _execute_single_tool_call(
    tool_map: Dict[str, BaseTool],
    fn_name: str,
    raw_args: Any,
    exec_ctx: ExecutionContext,
    step: int,
    tool_timeout_seconds: float,
) -> Tuple[Dict[str, Any], ToolCallTrace, Optional[str]]:
    """Menjalankan satu tool call dengan validasi, timeout, dan error trapping."""
    parsed_args: Dict[str, Any] = {}
    tool_out_obj: Any = None
    err_msg: Optional[str] = None

    try:
        parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
        tool = _resolve_tool(tool_map, fn_name)
        if tool is None:
            raise ValueError(f"Tool '{fn_name}' tidak terdaftar dalam allowed_tools.")

        validated_in = tool.validate_inputs(parsed_args)
        validated_cfg = tool.validate_config({})
        raw_result = await asyncio.wait_for(
            tool.execute(validated_cfg, validated_in, exec_ctx),
            timeout=tool_timeout_seconds,
        )
        tool_out_obj = raw_result.model_dump() if isinstance(raw_result, BaseModel) else raw_result
        serialized = json.dumps(tool_out_obj, ensure_ascii=False, default=str)
        tool_content = _truncate_output(serialized)
    except asyncio.TimeoutError:
        err_msg = f"Tool '{fn_name}' melewati batas waktu {tool_timeout_seconds}s"
        logger.error(f"{err_msg} pada step {step}")
        tool_content = json.dumps({"error": err_msg}, ensure_ascii=False)
    except Exception as exc:
        err_msg = str(exc)
        logger.error(f"Error eksekusi tool '{fn_name}' pada step {step}: {err_msg}")
        tool_content = json.dumps({"error": err_msg}, ensure_ascii=False)

    trace = ToolCallTrace(
        step=step,
        tool_name=fn_name,
        arguments=parsed_args,
        output=tool_out_obj,
        error=err_msg,
    )
    return {"content": tool_content}, trace, err_msg


def _append_skipped_calls(messages: List[Dict[str, Any]], skipped_calls: List[Dict[str, Any]]) -> None:
    for extra in skipped_calls:
        messages.append({
            "role": "tool",
            "tool_call_id": extra["id"],
            "name": extra["name"],
            "content": json.dumps(
                {"error": f"Melebihi batas maksimal {MAX_TOOL_CALLS_PER_STEP} tool call per langkah."},
                ensure_ascii=False,
            ),
        })


async def _process_step_calls(
    active_calls: List[Dict[str, Any]],
    skipped_calls: List[Dict[str, Any]],
    tool_map: Dict[str, BaseTool],
    exec_ctx: ExecutionContext,
    step: int,
    tool_timeout_seconds: float,
    messages: List[Dict[str, Any]],
    traces: List[ToolCallTrace],
    total_calls: int,
) -> Tuple[int, Optional[AgentResult]]:
    """Memproses daftar tool calls dalam satu step; berhenti jika butuh approval."""
    for idx, call in enumerate(active_calls):
        fn_name = call["name"]
        raw_args = call["arguments"]
        tool = _resolve_tool(tool_map, fn_name)

        if _needs_approval(tool, exec_ctx):
            try:
                parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except Exception:
                parsed_args = {}
            pending = {
                "call_id": call["id"],
                "tool_name": fn_name,
                "arguments": parsed_args,
                "risk_tier": tool.metadata.risk_tier.value if tool else "UNKNOWN",
                "step": step,
                "remaining_calls": active_calls[idx + 1:],
                "skipped_calls": skipped_calls,
            }
            paused = AgentResult(
                status=AgentStatus.APPROVAL_REQUIRED,
                reply=f"Menunggu persetujuan untuk menjalankan tool '{fn_name}'.",
                steps_taken=step,
                tool_calls_count=total_calls,
                traces=traces,
                messages=messages,
                pending_approval=pending,
            )
            return total_calls, paused

        total_calls += 1
        res_payload, trace, _ = await _execute_single_tool_call(
            tool_map, fn_name, raw_args, exec_ctx, step, tool_timeout_seconds
        )
        traces.append(trace)
        messages.append({
            "role": "tool",
            "tool_call_id": call["id"],
            "name": fn_name,
            "content": res_payload["content"],
        })

    _append_skipped_calls(messages, skipped_calls)
    return total_calls, None


async def _run_loop_from_state(
    start_step: int,
    max_steps: int,
    allowed_tools: Sequence[BaseTool],
    llm_client: Optional[Any],
    model: str,
    exec_ctx: ExecutionContext,
    tool_timeout_seconds: float,
    messages: List[Dict[str, Any]],
    traces: List[ToolCallTrace],
    total_calls: int,
) -> AgentResult:
    if llm_client is None:
        return AgentResult(
            status=AgentStatus.LLM_UNAVAILABLE,
            reply="Provider LLM tidak tersedia.",
            steps_taken=max(0, start_step - 1),
            tool_calls_count=total_calls,
            traces=traces,
            messages=messages,
        )

    tool_map: Dict[str, BaseTool] = {t.metadata.tool_type: t for t in allowed_tools}
    tools_payload = [t.to_openai_tool() for t in allowed_tools]

    for step in range(start_step, max_steps + 1):
        try:
            create_kwargs: Dict[str, Any] = {
                "model": model,
                "messages": messages,
            }
            if tools_payload:
                create_kwargs["tools"] = tools_payload
                create_kwargs["tool_choice"] = "auto"
            response = await llm_client.chat.completions.create(**create_kwargs)
        except Exception as exc:
            logger.error(f"Gagal memanggil LLM pada step {step}: {exc}")
            return AgentResult(
                status=AgentStatus.LLM_UNAVAILABLE,
                reply=f"Provider LLM gagal merespons: {exc}",
                steps_taken=step - 1,
                tool_calls_count=total_calls,
                traces=traces,
                messages=messages,
            )

        msg = response.choices[0].message
        messages.append(_serialize_assistant_message(msg))

        raw_tool_calls = getattr(msg, "tool_calls", None) or []
        if not raw_tool_calls:
            return AgentResult(
                status=AgentStatus.DONE,
                reply=(getattr(msg, "content", None) or "").strip(),
                steps_taken=step,
                tool_calls_count=total_calls,
                traces=traces,
                messages=messages,
            )

        serialized = [_serialize_call(c) for c in raw_tool_calls]
        active_calls = serialized[:MAX_TOOL_CALLS_PER_STEP]
        skipped_calls = serialized[MAX_TOOL_CALLS_PER_STEP:]

        total_calls, paused = await _process_step_calls(
            active_calls=active_calls,
            skipped_calls=skipped_calls,
            tool_map=tool_map,
            exec_ctx=exec_ctx,
            step=step,
            tool_timeout_seconds=tool_timeout_seconds,
            messages=messages,
            traces=traces,
            total_calls=total_calls,
        )
        if paused is not None:
            return paused

    return AgentResult(
        status=AgentStatus.MAX_STEPS,
        reply=f"Loop dihentikan karena mencapai batas maksimal {max_steps} langkah.",
        steps_taken=max_steps,
        tool_calls_count=total_calls,
        traces=traces,
        messages=messages,
    )


async def run_agent(
    goal: str,
    allowed_tools: Sequence[BaseTool],
    max_steps: int = DEFAULT_MAX_STEPS,
    session: Optional[AsyncSession] = None,
    *,
    llm_client: Optional[Any] = None,
    model: str = "deepseek-chat",
    system_prompt: str = "Anda adalah asisten bisnis JuraganAI yang jujur, ringkas, dan berbasis data tool.",
    history_messages: Optional[List[Dict[str, Any]]] = None,
    context: Optional[ExecutionContext] = None,
    tool_timeout_seconds: float = DEFAULT_TOOL_TIMEOUT_SECONDS,
) -> AgentResult:
    """Menjalankan loop ReAct dengan batas langkah, batas tool call per step, timeout, dan gate approval."""
    if max_steps <= 0:
        raise ValueError("max_steps harus lebih besar dari 0")

    exec_ctx = _build_tool_context(context, session)
    messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    if history_messages:
        messages.extend(history_messages)
    messages.append({"role": "user", "content": goal})

    return await _run_loop_from_state(
        start_step=1,
        max_steps=max_steps,
        allowed_tools=allowed_tools,
        llm_client=llm_client,
        model=model,
        exec_ctx=exec_ctx,
        tool_timeout_seconds=tool_timeout_seconds,
        messages=messages,
        traces=[],
        total_calls=0,
    )


async def resume_with_approval(
    result: AgentResult,
    allowed_tools: Sequence[BaseTool],
    approved: bool = True,
    max_steps: int = DEFAULT_MAX_STEPS,
    session: Optional[AsyncSession] = None,
    *,
    llm_client: Optional[Any] = None,
    model: str = "deepseek-chat",
    context: Optional[ExecutionContext] = None,
    tool_timeout_seconds: float = DEFAULT_TOOL_TIMEOUT_SECONDS,
) -> AgentResult:
    """Melanjutkan atau menolak eksekusi tool yang tertunda pada APPROVAL_REQUIRED."""
    if result.status != AgentStatus.APPROVAL_REQUIRED or not result.pending_approval:
        raise ValueError("AgentResult tidak berada dalam status APPROVAL_REQUIRED.")

    pending = result.pending_approval
    if not approved:
        return AgentResult(
            status=AgentStatus.REJECTED,
            reply=f"Eksekusi tool '{pending.get('tool_name')}' ditolak oleh pengguna.",
            steps_taken=result.steps_taken,
            tool_calls_count=result.tool_calls_count,
            traces=list(result.traces),
            messages=list(result.messages),
            pending_approval=None,
        )

    base_ctx = _build_tool_context(context, session)
    approved_meta = dict(base_ctx.extra_meta)
    approved_meta["action_approved"] = True
    approved_ctx = base_ctx.model_copy(update={"extra_meta": approved_meta})

    tool_map: Dict[str, BaseTool] = {t.metadata.tool_type: t for t in allowed_tools}
    messages = list(result.messages)
    traces = list(result.traces)
    total_calls = result.tool_calls_count + 1
    paused_step = int(pending.get("step") or result.steps_taken or 1)

    res_payload, trace, _ = await _execute_single_tool_call(
        tool_map=tool_map,
        fn_name=pending["tool_name"],
        raw_args=pending.get("arguments", {}),
        exec_ctx=approved_ctx,
        step=paused_step,
        tool_timeout_seconds=tool_timeout_seconds,
    )
    traces.append(trace)
    messages.append({
        "role": "tool",
        "tool_call_id": pending["call_id"],
        "name": pending["tool_name"],
        "content": res_payload["content"],
    })

    remaining_calls = list(pending.get("remaining_calls") or [])
    skipped_calls = list(pending.get("skipped_calls") or [])
    if remaining_calls or skipped_calls:
        total_calls, paused_again = await _process_step_calls(
            active_calls=remaining_calls,
            skipped_calls=skipped_calls,
            tool_map=tool_map,
            exec_ctx=base_ctx,
            step=paused_step,
            tool_timeout_seconds=tool_timeout_seconds,
            messages=messages,
            traces=traces,
            total_calls=total_calls,
        )
        if paused_again is not None:
            return paused_again

    return await _run_loop_from_state(
        start_step=paused_step + 1,
        max_steps=max_steps,
        allowed_tools=allowed_tools,
        llm_client=llm_client,
        model=model,
        exec_ctx=base_ctx,
        tool_timeout_seconds=tool_timeout_seconds,
        messages=messages,
        traces=traces,
        total_calls=total_calls,
    )
