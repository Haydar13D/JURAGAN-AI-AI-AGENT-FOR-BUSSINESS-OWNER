import json
import uuid
from datetime import datetime, date, timedelta
from typing import Dict, List, Any, Optional, Tuple, Literal
from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import logger
from app.core.tool_registry import (
    BaseTool,
    ExecutionContext,
    ToolCategory,
    ToolMetadata,
    ToolRegistry,
    ToolRiskTier,
)
from app.models.chat_session import ChatSession, ChatMessage
from app.services.analytics_engine import SalesAnalyticsEngine
from app.schemas.agent import (
    AgentChatResponse,
    ToolExecutionRecord,
    ConversationHistoryResponse,
    ConversationMessageItem,
    ConversationResetResponse
)


class LLMUnavailableError(Exception):
    """Dilempar ketika provider LLM tidak tersedia atau seluruh provider gagal merespons."""
    pass


# 1. Definisi Analytics Tools sebagai BaseTool di ToolRegistry tunggal
class DateRangeInput(BaseModel):
    start_date: str = Field(..., description="Tanggal awal periode (YYYY-MM-DD)")
    end_date: str = Field(..., description="Tanggal akhir periode (YYYY-MM-DD)")


class TopProductsInput(DateRangeInput):
    limit: int = Field(default=5, description="Jumlah menu teratas yang ingin ditampilkan")
    sort_by: Literal["qty", "revenue"] = Field(default="qty", description="Urutkan berdasarkan kuantitas ('qty') atau omzet ('revenue')")


class SlowMovingInput(DateRangeInput):
    limit: int = Field(default=5, description="Jumlah menu yang ditampilkan")


class PeakHoursInput(BaseModel):
    date: str = Field(..., description="Tanggal analisis dalam format YYYY-MM-DD")


class ComparePeriodInput(BaseModel):
    period_a_start: str = Field(..., description="Tanggal mulai periode pertama (YYYY-MM-DD)")
    period_a_end: str = Field(..., description="Tanggal akhir periode pertama (YYYY-MM-DD)")
    period_b_start: str = Field(..., description="Tanggal mulai periode kedua (YYYY-MM-DD)")
    period_b_end: str = Field(..., description="Tanggal akhir periode kedua (YYYY-MM-DD)")


class AnalyticsListResult(BaseModel):
    items: List[Dict[str, Any]] = Field(default_factory=list)


class AnalyticsDictResult(BaseModel):
    data: Dict[str, Any] = Field(default_factory=dict)


def _extract_db_ctx(context: ExecutionContext) -> Tuple[AsyncSession, int]:
    session = context.extra_meta.get("db_session")
    if session is None:
        raise ValueError("db_session wajib tersedia di ExecutionContext.extra_meta")
    outlet_id = int(context.extra_meta.get("outlet_id", 1))
    return session, outlet_id


class AnalyticsSalesOverviewTool(BaseTool):
    metadata = ToolMetadata(
        tool_type="analytics.get_sales_overview",
        version="1.0.0",
        display_name="Sales Overview",
        description="Mengambil ringkasan data omzet penjualan (GMV, diskon, net sales, jumlah transaksi, dan AOV) dalam rentang tanggal tertentu.",
        category=ToolCategory.ANALYSIS,
        risk_tier=ToolRiskTier.NONE,
        is_side_effect=False,
        requires_action_approval=False,
    )
    input_schema = DateRangeInput
    output_schema = BaseModel

    async def execute(self, config: BaseModel, inputs: DateRangeInput, context: ExecutionContext) -> BaseModel:
        session, outlet_id = _extract_db_ctx(context)
        return await SalesAnalyticsEngine.get_sales_overview(session, outlet_id, inputs.start_date, inputs.end_date)


class AnalyticsTopProductsTool(BaseTool):
    metadata = ToolMetadata(
        tool_type="analytics.get_top_products",
        version="1.0.0",
        display_name="Top Products",
        description="Mengambil daftar menu makanan/minuman terlaris berdasarkan kuantitas (qty) atau nominal omzet (revenue).",
        category=ToolCategory.ANALYSIS,
        risk_tier=ToolRiskTier.NONE,
        is_side_effect=False,
        requires_action_approval=False,
    )
    input_schema = TopProductsInput
    output_schema = AnalyticsListResult

    async def execute(self, config: BaseModel, inputs: TopProductsInput, context: ExecutionContext) -> AnalyticsListResult:
        session, outlet_id = _extract_db_ctx(context)
        res = await SalesAnalyticsEngine.get_top_products(
            session, outlet_id, inputs.start_date, inputs.end_date, inputs.limit, inputs.sort_by
        )
        return AnalyticsListResult(items=[item.model_dump() for item in res])


class AnalyticsSlowMovingTool(BaseTool):
    metadata = ToolMetadata(
        tool_type="analytics.get_slow_moving_products",
        version="1.0.0",
        display_name="Slow Moving Products",
        description="Mengambil daftar menu yang paling sepi pembeli atau deadstock untuk evaluasi stok dan menu.",
        category=ToolCategory.ANALYSIS,
        risk_tier=ToolRiskTier.NONE,
        is_side_effect=False,
        requires_action_approval=False,
    )
    input_schema = SlowMovingInput
    output_schema = AnalyticsListResult

    async def execute(self, config: BaseModel, inputs: SlowMovingInput, context: ExecutionContext) -> AnalyticsListResult:
        session, outlet_id = _extract_db_ctx(context)
        res = await SalesAnalyticsEngine.get_slow_moving_products(
            session, outlet_id, inputs.start_date, inputs.end_date, inputs.limit
        )
        return AnalyticsListResult(items=[item.model_dump() for item in res])


class AnalyticsPeakHoursTool(BaseTool):
    metadata = ToolMetadata(
        tool_type="analytics.get_peak_hours",
        version="1.0.0",
        display_name="Peak Hours",
        description="Menganalisis jam-jam paling ramai (peak hours) dan paling sepi pada tanggal tertentu.",
        category=ToolCategory.ANALYSIS,
        risk_tier=ToolRiskTier.NONE,
        is_side_effect=False,
        requires_action_approval=False,
    )
    input_schema = PeakHoursInput
    output_schema = AnalyticsListResult

    async def execute(self, config: BaseModel, inputs: PeakHoursInput, context: ExecutionContext) -> AnalyticsListResult:
        session, outlet_id = _extract_db_ctx(context)
        res = await SalesAnalyticsEngine.get_peak_hours(session, outlet_id, inputs.date)
        return AnalyticsListResult(items=[item.model_dump() for item in res])


class AnalyticsComparePeriodTool(BaseTool):
    metadata = ToolMetadata(
        tool_type="analytics.compare_sales_period",
        version="1.0.0",
        display_name="Compare Sales Period",
        description="Membandingkan performa penjualan antara dua periode berbeda.",
        category=ToolCategory.ANALYSIS,
        risk_tier=ToolRiskTier.NONE,
        is_side_effect=False,
        requires_action_approval=False,
    )
    input_schema = ComparePeriodInput
    output_schema = BaseModel

    async def execute(self, config: BaseModel, inputs: ComparePeriodInput, context: ExecutionContext) -> BaseModel:
        session, outlet_id = _extract_db_ctx(context)
        return await SalesAnalyticsEngine.compare_sales_period(
            session, outlet_id,
            inputs.period_a_start, inputs.period_a_end,
            inputs.period_b_start, inputs.period_b_end,
        )


class AnalyticsPaymentStatsTool(BaseTool):
    metadata = ToolMetadata(
        tool_type="analytics.get_payment_method_stats",
        version="1.0.0",
        display_name="Payment Method Stats",
        description="Mengambil persentase dan nominal penggunaan metode pembayaran (QRIS, Tunai, Kartu, dll).",
        category=ToolCategory.ANALYSIS,
        risk_tier=ToolRiskTier.NONE,
        is_side_effect=False,
        requires_action_approval=False,
    )
    input_schema = DateRangeInput
    output_schema = AnalyticsListResult

    async def execute(self, config: BaseModel, inputs: DateRangeInput, context: ExecutionContext) -> AnalyticsListResult:
        session, outlet_id = _extract_db_ctx(context)
        res = await SalesAnalyticsEngine.get_payment_method_stats(session, outlet_id, inputs.start_date, inputs.end_date)
        return AnalyticsListResult(items=[item.model_dump() for item in res])


class AnalyticsCategoryContribTool(BaseTool):
    metadata = ToolMetadata(
        tool_type="analytics.get_category_contribution",
        version="1.0.0",
        display_name="Category Contribution",
        description="Mengambil rincian kontribusi omzet dan kuantitas per kategori produk.",
        category=ToolCategory.ANALYSIS,
        risk_tier=ToolRiskTier.NONE,
        is_side_effect=False,
        requires_action_approval=False,
    )
    input_schema = DateRangeInput
    output_schema = AnalyticsListResult

    async def execute(self, config: BaseModel, inputs: DateRangeInput, context: ExecutionContext) -> AnalyticsListResult:
        session, outlet_id = _extract_db_ctx(context)
        res = await SalesAnalyticsEngine.get_category_contribution(session, outlet_id, inputs.start_date, inputs.end_date)
        return AnalyticsListResult(items=[item.model_dump() for item in res])


class AnalyticsDayOfWeekTool(BaseTool):
    metadata = ToolMetadata(
        tool_type="analytics.get_day_of_week_analysis",
        version="1.0.0",
        display_name="Day of Week Analysis",
        description="Menganalisis perbandingan performa rata-rata omzet hari kerja versus akhir pekan.",
        category=ToolCategory.ANALYSIS,
        risk_tier=ToolRiskTier.NONE,
        is_side_effect=False,
        requires_action_approval=False,
    )
    input_schema = DateRangeInput
    output_schema = BaseModel

    async def execute(self, config: BaseModel, inputs: DateRangeInput, context: ExecutionContext) -> BaseModel:
        session, outlet_id = _extract_db_ctx(context)
        return await SalesAnalyticsEngine.get_day_of_week_analysis(session, outlet_id, inputs.start_date, inputs.end_date)


def register_analytics_tools() -> None:
    """Mendaftarkan seluruh tool analitik BI ke ToolRegistry tunggal."""
    for tool_cls in (
        AnalyticsSalesOverviewTool,
        AnalyticsTopProductsTool,
        AnalyticsSlowMovingTool,
        AnalyticsPeakHoursTool,
        AnalyticsComparePeriodTool,
        AnalyticsPaymentStatsTool,
        AnalyticsCategoryContribTool,
        AnalyticsDayOfWeekTool,
    ):
        ToolRegistry.register(tool_cls())


register_analytics_tools()

class DeepSeekAgentService:

    @staticmethod
    def _build_system_prompt() -> str:
        today_str = datetime.now().strftime("%Y-%m-%d")
        now_time = datetime.now().strftime("%H:%M WIB")
        day_names = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
        today_day = day_names[datetime.now().weekday()]
        return f"""Anda adalah 'JuraganAI', asisten Business Intelligence cerdas dan konsultan bisnis F&B (kafe, resto, kedai) untuk pemilik usaha di Indonesia.
Hari ini adalah: {today_day}, {today_str}, pukul {now_time}.

PANDUAN & GUARDRAIL UTAMA:
1. Panggil tool yang sesuai untuk mengambil data aktual dari database kasir POS. DILARANG KERAS mengarang atau berhalusinasi angka omzet, persentase, atau daftar menu!
2. Jika pengguna menggunakan kata relatif seperti 'hari ini', 'kemarin', 'minggu ini', 'bulan ini', petakan ke rentang tanggal YYYY-MM-DD yang tepat berbasis hari ini ({today_str}).
3. Sampaikan jawaban dalam Bahasa Indonesia yang ramah, hangat, profesional, dan khas mitra bisnis kuliner ('Halo Juragan!', 'Mantap sekali!', dll).
4. STRUKTUR FORMAT JAWABAN:
   - Awali dengan sapaan hangat dan ringkasan langsung atas pertanyaan.
   - Sajikan metrik angka dengan jelas (format Rupiah misal Rp1.250.000, jumlah transaksi, kuantitas terjual) menggunakan bullet points yang rapi.
   - Di akhir jawaban, SELALU sertakan 1-2 tips aksi bisnis yang nyata (actionable insights), misalnya: saran promo bundling menu favorit dengan cemilan, saran stok bahan baku, atau optimasi jadwal staf pada jam sibuk.
5. Jika data kosong (0 transaksi), sampaikan dengan jujur bahwa belum ada data transaksi tercatat pada periode tersebut dan tanyakan apakah ada file transaksi baru yang ingin diunggah.
6. Gunakan riwayat percakapan dan hasil tool sebelumnya sebagai konteks penuh, namun jawab fokus pada pertanyaan terakhir pengguna tanpa mengulang jawaban lama yang tidak diminta."""

    @staticmethod
    def _reconstruct_history_messages(history_messages: List[ChatMessage]) -> List[Dict[str, Any]]:
        """Merekonstruksi riwayat percakapan lengkap: user -> assistant (+ tool_calls) -> tool -> assistant."""
        reconstructed: List[Dict[str, Any]] = []
        call_kind = "function"

        for hist in history_messages:
            content = hist.content or ""
            if hist.role == "user":
                reconstructed.append({"role": "user", "content": content})
            elif hist.role == "assistant":
                parsed_records = None
                if hist.tool_calls:
                    try:
                        raw = json.loads(hist.tool_calls)
                        if isinstance(raw, list) and len(raw) > 0:
                            parsed_records = raw
                    except Exception:
                        parsed_records = None

                if parsed_records:
                    assistant_calls = []
                    tool_msgs = []
                    for idx, rec in enumerate(parsed_records):
                        cid = rec.get("tool_call_id") or f"call_{getattr(hist, 'id', 'h')}_{idx}"
                        fn_name = rec.get("tool_name", "")
                        args_json = json.dumps(rec.get("arguments", {}), ensure_ascii=False)
                        assistant_calls.append({
                            "id": cid,
                            "type": call_kind,
                            "function": {"name": fn_name, "arguments": args_json},
                        })
                        tool_msgs.append({
                            "role": "tool",
                            "tool_call_id": cid,
                            "name": fn_name,
                            "content": json.dumps(rec.get("output"), ensure_ascii=False, default=str),
                        })
                    reconstructed.append({
                        "role": "assistant",
                        "content": "",
                        "tool_calls": assistant_calls,
                    })
                    reconstructed.extend(tool_msgs)

                reconstructed.append({"role": "assistant", "content": content})
            elif hist.role == "tool":
                reconstructed.append({"role": "tool", "content": content})

        return reconstructed

    @classmethod
    async def _compact_history_messages(
        cls,
        reconstructed: List[Dict[str, Any]],
        token_budget: int = 6000,
        llm_client: Optional[Any] = None,
        model: str = "deepseek-chat",
    ) -> List[Dict[str, Any]]:
        """Compaction berbasis budget token: pertahankan turn terakhir utuh, ringkas turn lama via LLM."""
        est_tokens = sum(len(json.dumps(m, ensure_ascii=False)) for m in reconstructed) // 4
        if est_tokens <= token_budget or len(reconstructed) <= 2:
            return reconstructed

        # Cari awal turn terakhir (pesan role='user' terakhir) agar dipertahankan utuh
        last_user_idx = 0
        for i in range(len(reconstructed) - 1, -1, -1):
            if reconstructed[i].get("role") == "user":
                last_user_idx = i
                break

        older = reconstructed[:last_user_idx]
        tail = reconstructed[last_user_idx:]
        if not older:
            return tail

        if llm_client is not None:
            try:
                summary_prompt = [
                    {
                        "role": "system",
                        "content": "Ringkas percakapan bisnis sebelumnya secara singkat dan akurat (angka & fakta utama saja) dalam Bahasa Indonesia.",
                    },
                    {
                        "role": "user",
                        "content": json.dumps(older, ensure_ascii=False),
                    },
                ]
                res = await llm_client.chat.completions.create(
                    model=model,
                    messages=summary_prompt,
                    temperature=0.1,
                )
                summary_text = (res.choices[0].message.content or "").strip()
                if summary_text:
                    return [{"role": "system", "content": f"Ringkasan percakapan sebelumnya:\n{summary_text}"}] + tail
            except Exception as exc:
                logger.error(f"Gagal meringkas riwayat percakapan: {exc}")

        return tail

    @staticmethod
    async def execute_tool(
        session: AsyncSession,
        outlet_id: int,
        tool_name: str,
        arguments: Dict[str, Any]
    ) -> Any:
        logger.info(f"Eksekusi Tool '{tool_name}' dengan argumen: {arguments}")
        if not ToolRegistry.is_allowlisted(tool_name) and "." not in tool_name:
            register_analytics_tools()
        resolved_name = tool_name if "." in tool_name else f"analytics.{tool_name}"
        tool = ToolRegistry.get(resolved_name)
        if not tool:
            raise ValueError(f"Tool tidak dikenal di ToolRegistry: {tool_name}")
        validated_input = tool.validate_inputs(arguments)
        validated_config = tool.validate_config({})
        ctx = ExecutionContext(
            tenant_id=f"outlet_{outlet_id}",
            workflow_id="agent_chat",
            workflow_version=1,
            run_id=uuid.uuid4().hex[:8],
            actor_id="deepseek_agent",
            extra_meta={"db_session": session, "outlet_id": outlet_id},
        )
        out = await tool.execute(validated_config, validated_input, ctx)
        dumped = out.model_dump()
        return dumped["items"] if isinstance(out, AnalyticsListResult) else dumped

    @classmethod
    async def get_or_create_session(
        cls,
        session: AsyncSession,
        outlet_id: int,
        conversation_id: Optional[str] = None,
        initial_title: Optional[str] = None
    ) -> Tuple[ChatSession, List[ChatMessage]]:
        """Mengambil sesi yang ada beserta riwayat pesan terakhir, atau membuat sesi baru."""
        if conversation_id:
            stmt = select(ChatSession).where(
                ChatSession.session_uuid == conversation_id,
                ChatSession.outlet_id == outlet_id
            )
            res = await session.execute(stmt)
            chat_session = res.scalar_one_or_none()
            if chat_session:
                stmt_msgs = (
                    select(ChatMessage)
                    .where(ChatMessage.session_id == chat_session.id)
                    .order_by(ChatMessage.id.desc())
                    .limit(40)
                )
                res_msgs = await session.execute(stmt_msgs)
                # Urutkan kronologis dari terlama ke terbaru
                history = list(reversed(res_msgs.scalars().all()))
                return chat_session, history

        # Buat sesi baru
        new_uuid = conversation_id or str(uuid.uuid4())
        chat_session = ChatSession(
            session_uuid=new_uuid,
            outlet_id=outlet_id,
            title=(initial_title[:60] if initial_title else "Percakapan Baru")
        )
        session.add(chat_session)
        await session.commit()
        await session.refresh(chat_session)
        return chat_session, []

    @classmethod
    async def save_turn(
        cls,
        session: AsyncSession,
        chat_session: ChatSession,
        user_message: str,
        assistant_reply: str,
        tool_records: Optional[List[ToolExecutionRecord]] = None
    ) -> None:
        """Menyimpan pesan pengguna dan balasan asisten ke database."""
        try:
            user_msg = ChatMessage(
                session_id=chat_session.id,
                role="user",
                content=user_message,
                tool_calls=None
            )
            session.add(user_msg)

            tool_calls_json = None
            if tool_records:
                tool_calls_json = json.dumps([r.model_dump() for r in tool_records], ensure_ascii=False)

            bot_msg = ChatMessage(
                session_id=chat_session.id,
                role="assistant",
                content=assistant_reply,
                tool_calls=tool_calls_json
            )
            session.add(bot_msg)
            await session.commit()
        except Exception as e:
            logger.warning(f"Gagal menyimpan riwayat chat ke DB: {e}")
            await session.rollback()

    @classmethod
    async def get_conversation_history(
        cls,
        session: AsyncSession,
        conversation_id: str,
        outlet_id: int = 1
    ) -> Optional[ConversationHistoryResponse]:
        """Mengambil seluruh riwayat percakapan untuk conversation_id tertentu."""
        stmt = select(ChatSession).where(
            ChatSession.session_uuid == conversation_id,
            ChatSession.outlet_id == outlet_id
        )
        res = await session.execute(stmt)
        chat_session = res.scalar_one_or_none()
        if not chat_session:
            return None

        stmt_msgs = (
            select(ChatMessage)
            .where(ChatMessage.session_id == chat_session.id)
            .order_by(ChatMessage.created_at.asc())
        )
        res_msgs = await session.execute(stmt_msgs)
        messages = res_msgs.scalars().all()

        return ConversationHistoryResponse(
            conversation_id=chat_session.session_uuid,
            outlet_id=chat_session.outlet_id,
            total_messages=len(messages),
            messages=[
                ConversationMessageItem(
                    role=m.role,
                    content=m.content,
                    tool_calls=m.tool_calls,
                    created_at=m.created_at.strftime("%Y-%m-%d %H:%M:%S") if m.created_at else ""
                )
                for m in messages
            ]
        )

    @classmethod
    async def reset_conversation(
        cls,
        session: AsyncSession,
        conversation_id: str,
        outlet_id: int = 1
    ) -> bool:
        """Menghapus sesi percakapan beserta seluruh pesan untuk mereset memori."""
        stmt = select(ChatSession).where(
            ChatSession.session_uuid == conversation_id,
            ChatSession.outlet_id == outlet_id
        )
        res = await session.execute(stmt)
        chat_session = res.scalar_one_or_none()
        if not chat_session:
            return False

        await session.delete(chat_session)
        await session.commit()
        logger.info(f"Sesi percakapan '{conversation_id}' berhasil direset.")
        return True

    @classmethod
    def _get_active_providers(cls) -> List[Dict[str, Any]]:
        """Mengembalikan daftar client provider LLM aktif secara berurutan untuk cascading fallback."""
        providers = []
        preferred = (settings.LLM_PROVIDER or "atmorouter").lower()

        atmorouter_entry = None
        if settings.ATMOROUTER_API_KEY and settings.ATMOROUTER_API_KEY.strip():
            atmorouter_entry = {
                "name": "AtmoRouter",
                "client": AsyncOpenAI(
                    api_key=settings.ATMOROUTER_API_KEY,
                    base_url=settings.ATMOROUTER_BASE_URL,
                    default_headers={
                        "HTTP-Referer": "http://localhost:8005",
                        "X-Title": "JuraganAI F&B Assistant"
                    }
                ),
                "model": settings.ATMOROUTER_MODEL or "atmo/deepseek-v4.1-flash"
            }

        openrouter_entry = None
        if settings.OPENROUTER_API_KEY and settings.OPENROUTER_API_KEY.strip():
            openrouter_entry = {
                "name": "OpenRouter",
                "client": AsyncOpenAI(
                    api_key=settings.OPENROUTER_API_KEY,
                    base_url=settings.OPENROUTER_BASE_URL,
                    default_headers={
                        "HTTP-Referer": "http://localhost:8005",
                        "X-Title": "JuraganAI F&B Assistant"
                    }
                ),
                "model": settings.OPENROUTER_MODEL or "deepseek/deepseek-chat"
            }

        deepseek_entry = None
        if settings.DEEPSEEK_API_KEY and settings.DEEPSEEK_API_KEY.strip():
            deepseek_entry = {
                "name": "DeepSeek",
                "client": AsyncOpenAI(
                    api_key=settings.DEEPSEEK_API_KEY,
                    base_url=settings.DEEPSEEK_BASE_URL
                ),
                "model": settings.DEEPSEEK_MODEL or "deepseek-chat"
            }

        all_entries = [
            ("atmorouter", atmorouter_entry),
            ("deepseek", deepseek_entry),
            ("openrouter", openrouter_entry)
        ]

        # Prioritaskan provider yang dipilih user
        for name, entry in all_entries:
            if name == preferred and entry is not None:
                providers.append(entry)

        # Tambahkan provider cadangan lainnya
        for name, entry in all_entries:
            if name != preferred and entry is not None:
                providers.append(entry)

        return providers

    @classmethod
    async def chat(
        cls,
        session: AsyncSession,
        user_message: str,
        outlet_id: int = 1,
        conversation_id: Optional[str] = None
    ) -> AgentChatResponse:
        system_prompt = cls._build_system_prompt()

        # Inisialisasi atau muat sesi percakapan & memori pesan sebelumnya
        chat_session, history_messages = await cls.get_or_create_session(
            session=session,
            outlet_id=outlet_id,
            conversation_id=conversation_id,
            initial_title=user_message
        )
        resolved_conv_id = chat_session.session_uuid

        providers = cls._get_active_providers()

        if not providers:
            logger.error("Tidak ada API Key LLM aktif.")
            raise LLMUnavailableError("Provider LLM tidak tersedia atau belum terkonfigurasi.")

        # Rekonstruksi riwayat percakapan utuh: user -> assistant (+ tool_calls) -> tool -> assistant
        reconstructed_history = cls._reconstruct_history_messages(history_messages)
        compacted_history = await cls._compact_history_messages(
            reconstructed_history,
            llm_client=providers[0]["client"],
            model=providers[0]["model"],
        )
        base_messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            *compacted_history,
            {"role": "user", "content": user_message},
        ]

        # Coba eksekusi dengan cascading fallback antar-provider
        for provider in providers:
            client = provider["client"]
            model = provider["model"]
            p_name = provider["name"]
            logger.info(f"Mencoba memproses query dengan LLM Provider: [{p_name}] model: {model}")

            tools_executed = []
            tool_details = []
            messages = list(base_messages)

            try:
                if not ToolRegistry.is_allowlisted("analytics.get_sales_overview"):
                    register_analytics_tools()
                tools_payload = [t.to_openai_tool() for t in ToolRegistry.get_all_latest()]

                # 1. Panggilan Pertama ke LLM
                response = await client.chat.completions.create(
                    model=model,
                    messages=messages,
                    tools=tools_payload,
                    tool_choice="auto",
                    temperature=0.3
                )

                msg = response.choices[0].message

                # Jika model meminta tool calls
                if msg.tool_calls:
                    messages.append(msg)

                    for tool_call in msg.tool_calls:
                        fn_name = tool_call.function.name
                        raw_args = tool_call.function.arguments
                        try:
                            args = json.loads(raw_args)
                        except Exception:
                            args = {}

                        tools_executed.append(fn_name)
                        # Eksekusi tool nyata
                        tool_output = await cls.execute_tool(session, outlet_id, fn_name, args)
                        tool_details.append(
                            ToolExecutionRecord(
                                tool_call_id=tool_call.id,
                                tool_name=fn_name,
                                arguments=args,
                                output=tool_output
                            )
                        )

                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "name": fn_name,
                            "content": json.dumps(tool_output, ensure_ascii=False)
                        })

                    # 2. Panggilan Kedua untuk Menyintesis Jawaban Akhir
                    final_response = await client.chat.completions.create(
                        model=model,
                        messages=messages,
                        temperature=0.4
                    )
                    final_content = final_response.choices[0].message.content or ""
                else:
                    final_content = msg.content or "Halo Juragan! Ada yang bisa saya bantu terkait analitik penjualan toko Anda?"

                # Simpan interaksi ke basis data
                await cls.save_turn(
                    session=session,
                    chat_session=chat_session,
                    user_message=user_message,
                    assistant_reply=final_content,
                    tool_records=tool_details
                )

                return AgentChatResponse(
                    reply=final_content,
                    tools_executed=tools_executed,
                    tool_details=tool_details,
                    conversation_id=resolved_conv_id,
                    provider_used=p_name,
                    model_used=model
                )

            except Exception as e:
                logger.warning(f"Error pada provider [{p_name}]: {e}. Mencoba fallback berikutnya...", exc_info=False)
                continue

        logger.error("Seluruh LLM provider eksternal gagal dihubungi.")
        raise LLMUnavailableError("Seluruh provider LLM gagal merespons.")
