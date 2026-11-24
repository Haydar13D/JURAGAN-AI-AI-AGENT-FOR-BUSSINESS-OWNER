"""
tests/test_whatsapp_tool.py
T19 — Menguji whatsapp.send_message:
(a) Fake client kirim 1x -> SENT
(b) Kirim ulang idempotency_key sama -> tetap 1x (deduplicated=True)
(c) Sandbox mode -> 0 network call, 1 record whatsapp_outbox
(d) Tanpa kredensial -> status NOT_CONFIGURED
(e) Input kosong/rusak ditolak oleh validate_inputs (I11)
"""

import uuid

from pydantic import ValidationError
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.database import AsyncSessionLocal, init_db
from app.core.tool_registry import EmptyConfig, ExecutionContext
from app.models.agent_run import WhatsappOutbox
from app.services.whatsapp_tool import WhatsappSendMessageInput, WhatsappSendMessageTool


def _make_ctx(session) -> ExecutionContext:
    return ExecutionContext(
        tenant_id="outlet_1",
        workflow_id="test_whatsapp",
        workflow_version=1,
        run_id=f"run_{uuid.uuid4().hex[:8]}",
        extra_meta={"db_session": session, "outlet_id": 1},
    )


@pytest.mark.asyncio
async def test_whatsapp_send_and_idempotency_dedup(monkeypatch):
    await init_db()
    monkeypatch.setattr(settings, "WHATSAPP_SANDBOX", False)
    monkeypatch.setattr(settings, "WHATSAPP_API_URL", "https://wa.example.test/send")
    monkeypatch.setattr(settings, "WHATSAPP_API_TOKEN", "secret-token")

    gateway_calls = []

    async def _fake_post(url, token, recipient, message, idempotency_key):
        gateway_calls.append({"recipient": recipient, "key": idempotency_key})
        return {"message_id": f"wamid_{len(gateway_calls)}"}

    monkeypatch.setattr(WhatsappSendMessageTool, "_post_to_gateway", staticmethod(_fake_post))

    tool = WhatsappSendMessageTool()
    idem_key = f"wa_key_{uuid.uuid4().hex[:10]}"
    inp = WhatsappSendMessageInput(
        recipient="6281234567890",
        message="Halo Juragan, laporan siap.",
        idempotency_key=idem_key,
    )

    async with AsyncSessionLocal() as session:
        # (a) Kirim pertama -> 1x network call
        res1 = await tool.execute(EmptyConfig(), inp, _make_ctx(session))
        assert res1.status == "SENT"
        assert res1.deduplicated is False
        assert res1.provider_message_id == "wamid_1"
        assert len(gateway_calls) == 1

        # (b) Kirim ulang dengan idempotency_key sama -> tetap 1x network call
        res2 = await tool.execute(EmptyConfig(), inp, _make_ctx(session))
        assert res2.status == "SENT"
        assert res2.deduplicated is True
        assert res2.provider_message_id == "wamid_1"
        assert len(gateway_calls) == 1


@pytest.mark.asyncio
async def test_whatsapp_sandbox_writes_outbox_without_network(monkeypatch):
    await init_db()
    monkeypatch.setattr(settings, "WHATSAPP_SANDBOX", True)

    network_calls = []

    async def _should_not_run(*args, **kwargs):
        network_calls.append(True)
        return {}

    monkeypatch.setattr(WhatsappSendMessageTool, "_post_to_gateway", staticmethod(_should_not_run))

    tool = WhatsappSendMessageTool()
    idem_key = f"wa_sandbox_{uuid.uuid4().hex[:10]}"
    inp = WhatsappSendMessageInput(
        recipient="628111222333",
        message="Pesan sandbox.",
        idempotency_key=idem_key,
    )

    async with AsyncSessionLocal() as session:
        res = await tool.execute(EmptyConfig(), inp, _make_ctx(session))
        assert res.status == "SANDBOX_QUEUED"
        assert res.deduplicated is False
        assert len(network_calls) == 0

        rows = list(
            (await session.execute(select(WhatsappOutbox).where(WhatsappOutbox.idempotency_key == idem_key)))
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].status == "SANDBOX_QUEUED"


@pytest.mark.asyncio
async def test_whatsapp_without_credentials_returns_not_configured(monkeypatch):
    await init_db()
    monkeypatch.setattr(settings, "WHATSAPP_SANDBOX", False)
    monkeypatch.setattr(settings, "WHATSAPP_API_URL", None)
    monkeypatch.setattr(settings, "WHATSAPP_API_TOKEN", None)

    tool = WhatsappSendMessageTool()
    inp = WhatsappSendMessageInput(
        recipient="628999888777",
        message="Pesan test",
        idempotency_key=f"wa_nocfg_{uuid.uuid4().hex[:10]}",
    )

    async with AsyncSessionLocal() as session:
        res = await tool.execute(EmptyConfig(), inp, _make_ctx(session))
        assert res.status == "NOT_CONFIGURED"
        assert res.provider_message_id is None


def test_whatsapp_rejects_empty_or_invalid_inputs():
    tool = WhatsappSendMessageTool()
    with pytest.raises(ValidationError):
        tool.validate_inputs({})
    with pytest.raises(ValidationError):
        tool.validate_inputs({"recipient": "12", "message": "", "idempotency_key": ""})
