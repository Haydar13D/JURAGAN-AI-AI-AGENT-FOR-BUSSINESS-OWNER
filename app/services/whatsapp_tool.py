"""
app/services/whatsapp_tool.py
T19 / I19 — Tool whatsapp.send_message dengan idempotensi, sandbox outbox, dan status NOT_CONFIGURED.
"""

import json
import uuid
from typing import Any, Dict, Optional

import httpx
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.logging import logger
from app.core.tool_registry import (
    BaseTool,
    EmptyConfig,
    ExecutionContext,
    ToolCategory,
    ToolMetadata,
    ToolRegistry,
    ToolRiskTier,
)
from app.models.agent_run import WhatsappOutbox


class WhatsappSendMessageInput(BaseModel):
    recipient: str = Field(..., min_length=6, description="Nomor WhatsApp tujuan dalam format internasional, contoh: '6281234567890'.")
    message: str = Field(..., min_length=1, description="Isi pesan teks WhatsApp yang akan dikirim.")
    idempotency_key: str = Field(..., min_length=1, description="Kunci unik idempotensi per aksi pengiriman agar retry tidak mengirim pesan ganda.")


class WhatsappSendMessageOutput(BaseModel):
    status: str  # SANDBOX_QUEUED | SENT | NOT_CONFIGURED
    idempotency_key: str
    recipient: str
    deduplicated: bool = False
    provider_message_id: Optional[str] = None
    detail: str = ""


class WhatsappSendMessageTool(BaseTool):
    metadata = ToolMetadata(
        tool_type="whatsapp.send_message",
        version="1.0.0",
        display_name="Send WhatsApp Message",
        description=(
            "Mengirim pesan WhatsApp ke penerima tertentu melalui gateway atau sandbox outbox. "
            "Gunakan hanya setelah persetujuan owner untuk mengirim notifikasi/laporan ke nomor WhatsApp. "
            "Wajib menyertakan recipient (contoh '6281234567890'), message, dan idempotency_key unik. "
            "Mengembalikan status ('SANDBOX_QUEUED', 'SENT', atau 'NOT_CONFIGURED'), deduplicated, dan provider_message_id."
        ),
        category=ToolCategory.EXPORT,
        risk_tier=ToolRiskTier.EXTERNAL,
        is_side_effect=True,
        requires_action_approval=True,
    )
    config_schema = EmptyConfig
    input_schema = WhatsappSendMessageInput
    output_schema = WhatsappSendMessageOutput

    @staticmethod
    async def _post_to_gateway(url: str, token: str, recipient: str, message: str, idempotency_key: str) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {token}", "Idempotency-Key": idempotency_key},
                json={"recipient": recipient, "message": message},
            )
            resp.raise_for_status()
            return resp.json() if resp.content else {}

    async def _run_with_session(
        self,
        session: AsyncSession,
        inputs: WhatsappSendMessageInput,
        outlet_id: int,
    ) -> WhatsappSendMessageOutput:
        existing = (
            await session.execute(
                select(WhatsappOutbox).where(WhatsappOutbox.idempotency_key == inputs.idempotency_key)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return WhatsappSendMessageOutput(
                status=existing.status,
                idempotency_key=existing.idempotency_key,
                recipient=existing.recipient,
                deduplicated=True,
                provider_message_id=existing.provider_message_id,
                detail="Hasil dikembalikan dari cache idempotency_key tanpa pengiriman ulang.",
            )

        if settings.WHATSAPP_SANDBOX:
            msg_id = f"sandbox_{uuid.uuid4().hex[:10]}"
            row = WhatsappOutbox(
                idempotency_key=inputs.idempotency_key,
                outlet_id=outlet_id,
                recipient=inputs.recipient,
                message=inputs.message,
                status="SANDBOX_QUEUED",
                provider_message_id=msg_id,
                response_json=json.dumps({"sandbox": True, "message_id": msg_id}),
            )
            session.add(row)
            await session.commit()
            return WhatsappSendMessageOutput(
                status="SANDBOX_QUEUED",
                idempotency_key=inputs.idempotency_key,
                recipient=inputs.recipient,
                deduplicated=False,
                provider_message_id=msg_id,
                detail="Mode WHATSAPP_SANDBOX aktif: pesan dicatat di whatsapp_outbox tanpa network call.",
            )

        api_url = (settings.WHATSAPP_API_URL or "").strip()
        api_token = (settings.WHATSAPP_API_TOKEN or "").strip()
        if not api_url or not api_token:
            return WhatsappSendMessageOutput(
                status="NOT_CONFIGURED",
                idempotency_key=inputs.idempotency_key,
                recipient=inputs.recipient,
                deduplicated=False,
                provider_message_id=None,
                detail="WHATSAPP_API_URL atau WHATSAPP_API_TOKEN belum dikonfigurasi.",
            )

        gateway_resp = await self._post_to_gateway(
            api_url, api_token, inputs.recipient, inputs.message, inputs.idempotency_key
        )
        msg_id = str(gateway_resp.get("message_id") or gateway_resp.get("id") or f"wa_{uuid.uuid4().hex[:10]}")
        row = WhatsappOutbox(
            idempotency_key=inputs.idempotency_key,
            outlet_id=outlet_id,
            recipient=inputs.recipient,
            message=inputs.message,
            status="SENT",
            provider_message_id=msg_id,
            response_json=json.dumps(gateway_resp, ensure_ascii=False, default=str),
        )
        session.add(row)
        await session.commit()
        logger.info(f"Pesan WhatsApp terkirim ke {inputs.recipient} (id={msg_id})")
        return WhatsappSendMessageOutput(
            status="SENT",
            idempotency_key=inputs.idempotency_key,
            recipient=inputs.recipient,
            deduplicated=False,
            provider_message_id=msg_id,
            detail="Pesan WhatsApp berhasil dikirim.",
        )

    async def execute(
        self,
        config: BaseModel,
        inputs: WhatsappSendMessageInput,
        context: ExecutionContext,
    ) -> WhatsappSendMessageOutput:
        outlet_id = int(context.extra_meta.get("outlet_id") or 1)
        db_session = context.extra_meta.get("db_session")
        if db_session is not None:
            return await self._run_with_session(db_session, inputs, outlet_id)
        async with AsyncSessionLocal() as session:
            return await self._run_with_session(session, inputs, outlet_id)


def register_whatsapp_tool() -> None:
    ToolRegistry.register(WhatsappSendMessageTool())
