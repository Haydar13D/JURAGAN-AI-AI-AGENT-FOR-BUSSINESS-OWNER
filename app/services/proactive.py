"""
app/services/proactive.py
T21 — Scheduler proaktif harian yang memanggil run_agent(), idempoten per tanggal+slot,
mencatat AgentRun dengan trigger='schedule', dan fail-safe saat LLM mati.
"""

from datetime import datetime
import time
from typing import Awaitable, Callable, List, Optional
import uuid
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.logging import logger
from app.core.tool_registry import ExecutionContext, ToolRegistry
from app.models.agent_run import ProactiveSlotRun
from app.services.agent_loop import AgentResult, AgentStatus, run_agent
from app.services.agent_trace import record_agent_run
from app.services.deepseek_agent import DeepSeekAgentService, register_analytics_tools
from app.services.memory_service import MemoryService, register_memory_tool

DEFAULT_PROACTIVE_GOAL = (
    "Ringkas performa penjualan hari ini berdasarkan data tool dan berikan 1 saran aksi bisnis yang paling berdampak."
)

SendChannelFn = Callable[[str], Awaitable[None]]


def parse_schedule_hours(raw_hours: Optional[str] = None) -> List[int]:
    val = raw_hours if raw_hours is not None else settings.PROACTIVE_SCHEDULE_HOURS
    hours: List[int] = []
    for part in str(val).split(","):
        s = part.strip()
        if not s:
            continue
        h = int(s)
        if 0 <= h <= 23:
            hours.append(h)
    return hours or [7, 21]


def parse_outlet_ids(raw_outlets: Optional[str] = None) -> List[int]:
    val = raw_outlets if raw_outlets is not None else settings.PROACTIVE_OUTLET_IDS
    outlets: List[int] = []
    for part in str(val).split(","):
        s = part.strip()
        if s:
            outlets.append(int(s))
    return outlets or [settings.DEFAULT_OUTLET_ID]


async def _default_send_channel(text: str, *, outlet_id: int, slot_date: str, slot_label: str) -> None:
    channel = (settings.PROACTIVE_CHANNEL or "telegram").lower()
    if channel == "whatsapp" and settings.PROACTIVE_WHATSAPP_RECIPIENT:
        from app.core.tool_registry import EmptyConfig
        from app.services.whatsapp_tool import WhatsappSendMessageInput, WhatsappSendMessageTool

        tool = WhatsappSendMessageTool()
        inp = WhatsappSendMessageInput(
            recipient=settings.PROACTIVE_WHATSAPP_RECIPIENT,
            message=text,
            idempotency_key=f"proactive_{slot_date}_{slot_label}_{outlet_id}",
        )
        ctx = ExecutionContext(
            tenant_id=f"outlet_{outlet_id}",
            workflow_id="proactive_scheduler",
            workflow_version=1,
            run_id=f"sched_{slot_date}_{slot_label}",
            extra_meta={"outlet_id": outlet_id},
        )
        await tool.execute(EmptyConfig(), inp, ctx)
        return

    if settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_DEFAULT_CHAT_ID:
        from app.bot.telegram_bot import TelegramBotManager

        await TelegramBotManager.send_message(settings.TELEGRAM_DEFAULT_CHAT_ID, text)


async def run_proactive_slot(
    session: AsyncSession,
    *,
    slot_label: str,
    outlet_id: Optional[int] = None,
    now_dt: Optional[datetime] = None,
    goal: str = DEFAULT_PROACTIVE_GOAL,
    send_fn: Optional[SendChannelFn] = None,
) -> Optional[str]:
    """Menjalankan satu slot proaktif secara idempoten. Mengembalikan run_id atau None jika skip."""
    resolved_outlet = outlet_id if outlet_id is not None else settings.DEFAULT_OUTLET_ID
    tz = ZoneInfo(settings.TIMEZONE)
    current_dt = now_dt or datetime.now(tz)
    slot_date = current_dt.date().isoformat()

    existing = (
        await session.execute(
            select(ProactiveSlotRun).where(
                ProactiveSlotRun.slot_date == slot_date,
                ProactiveSlotRun.slot_label == slot_label,
                ProactiveSlotRun.outlet_id == resolved_outlet,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        logger.info(
            f"Proactive slot ({slot_date}, {slot_label}, outlet={resolved_outlet}) sudah pernah berjalan; skip."
        )
        return None

    run_id = f"sched_{uuid.uuid4().hex[:12]}"
    providers = DeepSeekAgentService._get_active_providers()
    if not providers:
        reason = "LLM_UNAVAILABLE: Tidak ada provider LLM aktif."
        await _record_failed_slot(
            session=session,
            run_id=run_id,
            slot_date=slot_date,
            slot_label=slot_label,
            outlet_id=resolved_outlet,
            reason=reason,
            provider=None,
            model=None,
            latency_ms=0,
        )
        return run_id

    if not ToolRegistry.is_allowlisted("analytics.get_sales_overview"):
        register_analytics_tools()
    if not ToolRegistry.is_allowlisted("memory.remember"):
        register_memory_tool()
    allowed_tools = ToolRegistry.get_all_latest()

    system_prompt = DeepSeekAgentService._build_system_prompt()
    facts = await MemoryService.get_facts(session, resolved_outlet)
    ctx_block = MemoryService.format_context_block(facts)
    if ctx_block:
        system_prompt = f"{system_prompt}\n\n{ctx_block}"

    exec_ctx = ExecutionContext(
        tenant_id=f"outlet_{resolved_outlet}",
        workflow_id="proactive_scheduler",
        workflow_version=1,
        run_id=run_id,
        actor_id="proactive_scheduler",
        extra_meta={"db_session": session, "outlet_id": resolved_outlet},
    )

    last_reason = "LLM_UNAVAILABLE: Seluruh provider gagal merespons."
    last_provider: Optional[str] = None
    last_model: Optional[str] = None
    t0 = time.perf_counter()

    for provider in providers:
        last_provider = provider["name"]
        last_model = provider["model"]
        try:
            agent_res: AgentResult = await run_agent(
                goal=goal,
                allowed_tools=allowed_tools,
                session=session,
                llm_client=provider["client"],
                model=provider["model"],
                system_prompt=system_prompt,
                context=exec_ctx,
            )
            if agent_res.status == AgentStatus.LLM_UNAVAILABLE:
                last_reason = agent_res.reply or last_reason
                continue

            latency_ms = int((time.perf_counter() - t0) * 1000)
            await record_agent_run(
                session=session,
                run_id=run_id,
                conversation_id=None,
                outlet_id=resolved_outlet,
                status=agent_res.status.value,
                provider=provider["name"],
                model=provider["model"],
                steps_taken=agent_res.steps_taken,
                tool_calls_count=agent_res.tool_calls_count,
                latency_ms=latency_ms,
                prompt_tokens=agent_res.prompt_tokens,
                completion_tokens=agent_res.completion_tokens,
                trigger="schedule",
                traces=agent_res.traces,
            )

            reply_text = agent_res.reply or "Ringkasan proaktif selesai."
            if send_fn is not None:
                await send_fn(reply_text)
            else:
                await _default_send_channel(
                    reply_text,
                    outlet_id=resolved_outlet,
                    slot_date=slot_date,
                    slot_label=slot_label,
                )

            session.add(
                ProactiveSlotRun(
                    slot_date=slot_date,
                    slot_label=slot_label,
                    outlet_id=resolved_outlet,
                    run_id=run_id,
                    status="SENT",
                )
            )
            await session.commit()
            return run_id
        except Exception as exc:
            last_reason = str(exc)
            logger.error(f"Proactive run provider '{provider['name']}' gagal: {exc}")
            continue

    latency_ms = int((time.perf_counter() - t0) * 1000)
    await _record_failed_slot(
        session=session,
        run_id=run_id,
        slot_date=slot_date,
        slot_label=slot_label,
        outlet_id=resolved_outlet,
        reason=last_reason,
        provider=last_provider,
        model=last_model,
        latency_ms=latency_ms,
    )
    return run_id


async def _record_failed_slot(
    *,
    session: AsyncSession,
    run_id: str,
    slot_date: str,
    slot_label: str,
    outlet_id: int,
    reason: str,
    provider: Optional[str],
    model: Optional[str],
    latency_ms: int,
) -> None:
    logger.error(f"Proactive slot ({slot_date}, {slot_label}, outlet={outlet_id}) FAILED: {reason}")
    await record_agent_run(
        session=session,
        run_id=run_id,
        conversation_id=None,
        outlet_id=outlet_id,
        status="FAILED",
        provider=provider,
        model=model,
        steps_taken=0,
        tool_calls_count=0,
        latency_ms=latency_ms,
        prompt_tokens=None,
        completion_tokens=None,
        trigger="schedule",
        traces=[],
    )
    session.add(
        ProactiveSlotRun(
            slot_date=slot_date,
            slot_label=slot_label,
            outlet_id=outlet_id,
            run_id=run_id,
            status="FAILED",
            error_message=reason,
        )
    )
    await session.commit()


async def run_proactive_job(slot_label: str, now_dt: Optional[datetime] = None) -> None:
    """Entrypoint job APScheduler: fail-safe agar scheduler tidak pernah crash."""
    for oid in parse_outlet_ids():
        try:
            async with AsyncSessionLocal() as session:
                await run_proactive_slot(
                    session,
                    slot_label=slot_label,
                    outlet_id=oid,
                    now_dt=now_dt,
                )
        except Exception as exc:
            logger.error(f"Error tak terduga pada proactive job ({slot_label}, outlet={oid}): {exc}")


def register_proactive_jobs(scheduler) -> None:
    """Mendaftarkan job harian proaktif ke APScheduler berdasarkan config."""
    for hour in parse_schedule_hours():
        slot_label = f"{hour:02d}:00"
        scheduler.add_job(
            run_proactive_job,
            trigger=CronTrigger(hour=hour, minute=0, timezone=settings.TIMEZONE),
            args=[slot_label],
            id=f"proactive_agent_{hour:02d}",
            replace_existing=True,
        )
