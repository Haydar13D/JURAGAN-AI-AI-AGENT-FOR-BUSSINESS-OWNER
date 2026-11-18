"""
tests/test_conversation_roundtrip.py
T10 — Memory percakapan yang benar.
Memverifikasi:
1. Turn 1 memanggil tool -> tersimpan bersama tool_call_id.
2. Turn 2 merekonstruksi riwayat lengkap: user -> assistant (+ tool_calls) -> tool (tool_call_id cocok & output asli) -> assistant -> user baru.
3. Tidak ada prefix '[Pertanyaan Sesi Sebelumnya...]' dan tidak ada pemotongan [:150].
4. Token-budget compaction mempertahankan turn terakhir utuh dan meringkas turn lama via LLM.
"""

import json
import uuid
from types import SimpleNamespace

import pytest

from app.core.database import AsyncSessionLocal, init_db
from app.services.deepseek_agent import AnalyticsSalesOverviewTool, DeepSeekAgentService


@pytest.mark.asyncio
async def test_conversation_roundtrip_reconstructs_tool_role_messages(monkeypatch):
    await init_db()
    conv_id = f"conv_t10_{uuid.uuid4().hex[:8]}"
    captured_payloads = []
    turn_counter = {"n": 0}

    async def _fake_execute(self, config, inputs, context):
        return {"total_gmv": 1500000, "orders": 42, "period": inputs.model_dump()}

    class _FakeCompletions:
        async def create(self, **kwargs):
            captured_payloads.append(kwargs["messages"])
            turn_counter["n"] += 1
            if turn_counter["n"] == 1:
                # Turn 1, call 1: minta tool
                tc = SimpleNamespace(
                    id="call_turn1_001",
                    function=SimpleNamespace(
                        name="analytics.get_sales_overview",
                        arguments=json.dumps({"start_date": "2026-11-10", "end_date": "2026-11-17"}),
                    ),
                )
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[tc]))]
                )
            elif turn_counter["n"] == 2:
                # Turn 1, call 2: jawaban final turn 1 (lebih dari 150 karakter untuk uji tidak terpotong)
                long_reply = "Halo Juragan! Omzet minggu ini mencapai Rp1.500.000 dari 42 transaksi. " + ("Detail lengkap terjaga utuh. " * 6)
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=long_reply, tool_calls=None))]
                )
            else:
                # Turn 2: jawaban langsung
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content="Tentu, totalnya 42 order.", tool_calls=None))]
                )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions()))
    monkeypatch.setattr(
        DeepSeekAgentService,
        "_get_active_providers",
        classmethod(lambda cls: [{"name": "FakeLLM", "client": fake_client, "model": "fake-model"}]),
    )
    monkeypatch.setattr(AnalyticsSalesOverviewTool, "execute", _fake_execute)

    async with AsyncSessionLocal() as session:
        # Turn 1
        r1 = await DeepSeekAgentService.chat(
            session=session,
            user_message="Berapa omzet minggu ini?",
            outlet_id=1,
            conversation_id=conv_id,
        )
        assert "1.500.000" in r1.reply

        # Turn 2 pada conversation_id yang sama
        r2 = await DeepSeekAgentService.chat(
            session=session,
            user_message="Berapa jumlah ordernya tadi?",
            outlet_id=1,
            conversation_id=conv_id,
        )
        assert "42" in r2.reply

    # Payload terakhir yang dikirim ke LLM pada Turn 2
    turn2_messages = captured_payloads[-1]

    # Pastikan tidak ada prefix atau potongan [:150]
    all_contents = json.dumps(turn2_messages, ensure_ascii=False)
    assert "Pertanyaan Sesi Sebelumnya" not in all_contents
    assert "Sudah selesai dijawab oleh asisten" not in all_contents
    assert ("Detail lengkap terjaga utuh. " * 6).strip() in all_contents

    # Pastikan ada message role='tool' dengan tool_call_id cocok dan output asli turn 1
    tool_msgs = [m for m in turn2_messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["tool_call_id"] == "call_turn1_001"
    assert json.loads(tool_msgs[0]["content"])["total_gmv"] == 1500000

    # Pastikan ada assistant message dengan tool_calls sebelum role='tool'
    assistant_with_calls = [m for m in turn2_messages if m.get("role") == "assistant" and m.get("tool_calls")]
    assert len(assistant_with_calls) == 1
    assert assistant_with_calls[0]["tool_calls"][0]["id"] == "call_turn1_001"


@pytest.mark.asyncio
async def test_token_budget_compaction_keeps_last_turn_intact():
    class _SummaryCompletions:
        async def create(self, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="Ringkasan: omzet kemarin Rp500.000."))]
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=_SummaryCompletions()))
    reconstructed = [
        {"role": "user", "content": "Pesan lama " + ("A" * 400)},
        {"role": "assistant", "content": "Jawaban lama " + ("B" * 400)},
        {"role": "user", "content": "Pertanyaan terakhir utuh"},
        {"role": "assistant", "content": "Jawaban terakhir utuh"},
    ]

    compacted = await DeepSeekAgentService._compact_history_messages(
        reconstructed,
        token_budget=50,
        llm_client=fake_client,
        model="fake-model",
    )

    assert compacted[0]["role"] == "system"
    assert "Ringkasan" in compacted[0]["content"]
    assert compacted[1:] == reconstructed[2:]
