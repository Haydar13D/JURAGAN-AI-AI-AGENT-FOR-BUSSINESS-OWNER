"""
tests/test_proactive.py
T21 — Menguji scheduler proaktif:
(a) Fake clock + fake channel: 1 slot -> 1 AgentRun (trigger='schedule') + 1 pesan terkirim
(b) Dua kali slot & tanggal sama -> tetap 1 run, 1 pesan (idempoten)
(c) Provider LLM mati -> AgentRun status FAILED, tidak ada exception lolos dari job
"""

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete, select

from app.core.config import settings
from app.core.database import AsyncSessionLocal, init_db
from app.models.agent_run import AgentRun, ProactiveSlotRun
from app.services.deepseek_agent import DeepSeekAgentService
from app.services.proactive import (
    parse_outlet_ids,
    parse_schedule_hours,
    run_proactive_job,
    run_proactive_slot,
)


@pytest.mark.asyncio
async def test_proactive_slot_idempotent_with_fake_clock_and_channel(monkeypatch):
    await init_db()
    test_outlet = 9701
    fake_now = datetime(2026, 11, 24, 7, 0, tzinfo=ZoneInfo("Asia/Jakarta"))
    sent_messages = []

    async def _fake_send(msg: str) -> None:
        sent_messages.append(msg)

    class _FakeCompletions:
        async def create(self, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="Ringkasan pagi: omzet aman.", tool_calls=None))]
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions()))
    monkeypatch.setattr(
        DeepSeekAgentService,
        "_get_active_providers",
        classmethod(lambda cls: [{"name": "FakeLLM", "client": fake_client, "model": "fake-model"}]),
    )

    async with AsyncSessionLocal() as session:
        await session.execute(delete(ProactiveSlotRun).where(ProactiveSlotRun.outlet_id == test_outlet))
        await session.execute(delete(AgentRun).where(AgentRun.outlet_id == test_outlet))
        await session.commit()

        # (a) Eksekusi pertama -> 1 run + 1 pesan
        run_id_1 = await run_proactive_slot(
            session,
            slot_label="07:00",
            outlet_id=test_outlet,
            now_dt=fake_now,
            send_fn=_fake_send,
        )
        assert run_id_1 is not None
        assert sent_messages == ["Ringkasan pagi: omzet aman."]

        # (b) Eksekusi kedua pada tanggal & slot sama -> skip, tetap 1 run & 1 pesan
        run_id_2 = await run_proactive_slot(
            session,
            slot_label="07:00",
            outlet_id=test_outlet,
            now_dt=fake_now,
            send_fn=_fake_send,
        )
        assert run_id_2 is None
        assert len(sent_messages) == 1

        runs = list((await session.execute(select(AgentRun).where(AgentRun.outlet_id == test_outlet))).scalars().all())
        assert len(runs) == 1
        assert runs[0].trigger == "schedule"
        assert runs[0].status == "DONE"


@pytest.mark.asyncio
async def test_proactive_provider_down_records_failed_without_raising(monkeypatch):
    await init_db()
    test_outlet = 9702
    fake_now = datetime(2026, 11, 24, 21, 0, tzinfo=ZoneInfo("Asia/Jakarta"))

    monkeypatch.setattr(DeepSeekAgentService, "_get_active_providers", classmethod(lambda cls: []))
    monkeypatch.setattr(settings, "PROACTIVE_OUTLET_IDS", str(test_outlet))

    async with AsyncSessionLocal() as session:
        await session.execute(delete(ProactiveSlotRun).where(ProactiveSlotRun.outlet_id == test_outlet))
        await session.execute(delete(AgentRun).where(AgentRun.outlet_id == test_outlet))
        await session.commit()

    # (c) Tidak boleh melempar exception saat LLM mati
    await run_proactive_job(slot_label="21:00", now_dt=fake_now)

    async with AsyncSessionLocal() as session:
        runs = list((await session.execute(select(AgentRun).where(AgentRun.outlet_id == test_outlet))).scalars().all())
        assert len(runs) == 1
        assert runs[0].status == "FAILED"
        assert runs[0].trigger == "schedule"


def test_proactive_reads_hours_and_outlets_from_config(monkeypatch):
    monkeypatch.setattr(settings, "PROACTIVE_SCHEDULE_HOURS", "6, 12, 20")
    monkeypatch.setattr(settings, "PROACTIVE_OUTLET_IDS", "1, 3")
    assert parse_schedule_hours() == [6, 12, 20]
    assert parse_outlet_ids() == [1, 3]
