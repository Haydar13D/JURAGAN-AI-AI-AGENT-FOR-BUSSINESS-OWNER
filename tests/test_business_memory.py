"""
tests/test_business_memory.py
T20 — Menguji business_facts, memory.remember, allowlist key, anti-PII, dan batas 500 token:
- Simpan fakta via memory.remember -> chat baru (beda conversation_id) memuat blok KONTEKS BISNIS
- Isi 30 fakta -> blok KONTEKS BISNIS tetap <= 500 token (prioritas updated_at terbaru)
- Key di luar allowlist ditolak
- PII pelanggan (nomor HP, email, nama pembeli) ditolak
"""

from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import delete

from app.core.database import AsyncSessionLocal, init_db
from app.models.business_memory import BusinessFact
from app.services.deepseek_agent import DeepSeekAgentService
from app.services.memory_service import MemoryService, estimate_tokens


@pytest.mark.asyncio
async def test_remember_fact_injected_into_next_chat_system_prompt(monkeypatch):
    await init_db()
    test_outlet_id = 9801
    captured_system_prompts = []
    call_idx = {"n": 0}

    class _FakeCompletions:
        async def create(self, **kwargs):
            call_idx["n"] += 1
            captured_system_prompts.append(kwargs["messages"][0]["content"])
            if call_idx["n"] == 1:
                tc = SimpleNamespace(
                    id="call_mem_1",
                    function=SimpleNamespace(
                        name="memory__remember",
                        arguments=json.dumps({
                            "key": "menu_andalan",
                            "value": "Kopi Susu Gula Aren",
                        }),
                    ),
                )
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[tc]))]
                )
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="Siap Juragan, sudah dicatat.", tool_calls=None))]
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions()))
    monkeypatch.setattr(
        DeepSeekAgentService,
        "_get_active_providers",
        classmethod(lambda cls: [{"name": "FakeLLM", "client": fake_client, "model": "fake-model"}]),
    )

    async with AsyncSessionLocal() as session:
        await session.execute(delete(BusinessFact).where(BusinessFact.outlet_id == test_outlet_id))
        await session.commit()

        # Chat 1: simpan fakta lewat tool memory.remember
        res1 = await DeepSeekAgentService.chat(
            session=session,
            user_message="Ingat ya menu andalan kami adalah Kopi Susu Gula Aren.",
            outlet_id=test_outlet_id,
            conversation_id=f"mem_conv1_{uuid.uuid4().hex[:6]}",
        )
        assert "memory.remember" in res1.tools_executed

        # Chat 2 dengan conversation_id baru (bukan dari history chat) -> wajib memuat KONTEKS BISNIS
        await DeepSeekAgentService.chat(
            session=session,
            user_message="Halo, apa kabar?",
            outlet_id=test_outlet_id,
            conversation_id=f"mem_conv2_{uuid.uuid4().hex[:6]}",
        )

    last_sys = captured_system_prompts[-1]
    assert "KONTEKS BISNIS:" in last_sys
    assert "menu_andalan: Kopi Susu Gula Aren" in last_sys


@pytest.mark.asyncio
async def test_thirty_facts_context_block_capped_at_500_tokens():
    await init_db()
    test_outlet_id = 9802
    base_time = datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc)

    async with AsyncSessionLocal() as session:
        await session.execute(delete(BusinessFact).where(BusinessFact.outlet_id == test_outlet_id))
        for i in range(30):
            session.add(
                BusinessFact(
                    outlet_id=test_outlet_id,
                    key="menu_andalan",
                    value=f"Fakta_bisnis_ke_{i:02d}_" + ("x" * 120),
                    confidence=1.0,
                    source="test",
                    updated_at=base_time + timedelta(minutes=i),
                )
            )
        await session.commit()

        facts = await MemoryService.get_facts(session, test_outlet_id)
        assert len(facts) == 30

        block = MemoryService.format_context_block(facts)
        assert estimate_tokens(block) <= 500
        # Fakta terbaru (ke-29) harus diprioritaskan, fakta terlama (ke-00) terpotong
        assert "Fakta_bisnis_ke_29" in block
        assert "Fakta_bisnis_ke_00" not in block


@pytest.mark.asyncio
async def test_disallowed_key_and_pii_are_rejected():
    await init_db()
    async with AsyncSessionLocal() as session:
        with pytest.raises(ValueError, match="tidak didukung"):
            await MemoryService.upsert_fact(
                session, outlet_id=9803, key="password_wifi", value="kopi123"
            )

        for pii_value in (
            "Hubungi 081234567890 untuk pesan",
            "Pelanggan setia budi@example.com",
            "Nama pembeli: Pak Budi langganan pagi",
        ):
            with pytest.raises(ValueError, match="PII"):
                await MemoryService.upsert_fact(
                    session, outlet_id=9803, key="supplier_utama", value=pii_value
                )
