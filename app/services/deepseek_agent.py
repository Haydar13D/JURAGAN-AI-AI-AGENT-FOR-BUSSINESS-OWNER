import json
import uuid
from datetime import datetime, date, timedelta
from typing import Dict, List, Any, Optional, Tuple
from openai import AsyncOpenAI
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import logger
from app.models.chat_session import ChatSession, ChatMessage
from app.services.analytics_engine import SalesAnalyticsEngine
from app.schemas.agent import (
    AgentChatResponse,
    ToolExecutionRecord,
    ConversationHistoryResponse,
    ConversationMessageItem,
    ConversationResetResponse
)

# 1. Definisi Skema Tools untuk DeepSeek Function Calling
AGENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_sales_overview",
            "description": "Mengambil ringkasan data omzet penjualan (GMV, diskon, net sales, jumlah transaksi, dan Average Order Value / AOV) dalam rentang tanggal tertentu.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {
                        "type": "string",
                        "description": "Tanggal awal dalam format YYYY-MM-DD"
                    },
                    "end_date": {
                        "type": "string",
                        "description": "Tanggal akhir dalam format YYYY-MM-DD"
                    }
                },
                "required": ["start_date", "end_date"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_top_products",
            "description": "Mengambil daftar menu makanan/minuman terlaris berdasarkan kuantitas (qty) atau nominal omzet (revenue).",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {
                        "type": "string",
                        "description": "Tanggal awal periode (YYYY-MM-DD)"
                    },
                    "end_date": {
                        "type": "string",
                        "description": "Tanggal akhir periode (YYYY-MM-DD)"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Jumlah menu teratas yang ingin ditampilkan (default: 5)"
                    },
                    "sort_by": {
                        "type": "string",
                        "enum": ["qty", "revenue"],
                        "description": "Urutkan berdasarkan kuantitas ('qty') atau omzet ('revenue')"
                    }
                },
                "required": ["start_date", "end_date"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_slow_moving_products",
            "description": "Mengambil daftar menu yang paling sepi pembeli atau deadstock untuk evaluasi stok dan menu.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {
                        "type": "string",
                        "description": "Tanggal awal periode (YYYY-MM-DD)"
                    },
                    "end_date": {
                        "type": "string",
                        "description": "Tanggal akhir periode (YYYY-MM-DD)"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Jumlah menu yang ditampilkan (default: 5)"
                    }
                },
                "required": ["start_date", "end_date"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_peak_hours",
            "description": "Menganalisis jam-jam paling ramai (peak hours) dan paling sepi pada tanggal tertentu.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Tanggal analisis dalam format YYYY-MM-DD"
                    }
                },
                "required": ["date"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "compare_sales_period",
            "description": "Membandingkan performa penjualan antara dua periode berbeda (contoh: minggu ini vs minggu lalu, atau kemarin vs hari ini).",
            "parameters": {
                "type": "object",
                "properties": {
                    "period_a_start": {"type": "string", "description": "Tanggal mulai periode pertama (YYYY-MM-DD)"},
                    "period_a_end": {"type": "string", "description": "Tanggal akhir periode pertama (YYYY-MM-DD)"},
                    "period_b_start": {"type": "string", "description": "Tanggal mulai periode kedua (YYYY-MM-DD)"},
                    "period_b_end": {"type": "string", "description": "Tanggal akhir periode kedua (YYYY-MM-DD)"}
                },
                "required": ["period_a_start", "period_a_end", "period_b_start", "period_b_end"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_payment_method_stats",
            "description": "Mengambil persentase dan nominal penggunaan metode pembayaran (QRIS, Tunai, Kartu, dll).",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "Tanggal awal periode (YYYY-MM-DD)"},
                    "end_date": {"type": "string", "description": "Tanggal akhir periode (YYYY-MM-DD)"}
                },
                "required": ["start_date", "end_date"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_category_contribution",
            "description": "Mengambil rincian kontribusi omzet dan kuantitas per kategori produk (Kopi, Non-Kopi, Makanan, Cemilan, Pastry, dll).",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "Tanggal awal YYYY-MM-DD"},
                    "end_date": {"type": "string", "description": "Tanggal akhir YYYY-MM-DD"}
                },
                "required": ["start_date", "end_date"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_day_of_week_analysis",
            "description": "Menganalisis perbandingan performa rata-rata omzet hari kerja (Senin-Kamis) versus akhir pekan (Jumat-Minggu).",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "Tanggal awal YYYY-MM-DD"},
                    "end_date": {"type": "string", "description": "Tanggal akhir YYYY-MM-DD"}
                },
                "required": ["start_date", "end_date"]
            }
        }
    }
]

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
6. ATURAN STRICT ISOLASI GILIRAN & ANTI-REPETISI (SANGAT KRUSIAL):
   - JAWAB HANYA PESAN USER TERAKHIR YANG TIDAK MEMILIKI AWALAN '[Pertanyaan Sesi Sebelumnya]'.
   - Pesan berawalan '[Pertanyaan Sesi Sebelumnya - SUDAH SELESAI DIJAWAB]' HANYALAH referensi konteks kata ganti jika user menggunakan kata seperti 'itu', 'tadi', atau 'bandingkan'.
   - DILARANG KERAS mengulang kembali jawaban atau mengeksekusi tool untuk pertanyaan sebelumnya!
   - DILARANG KERAS menggunakan frasa pembuka seperti 'Dua pertanyaan sekaligus ya' atau 'Dua hal ini saya jawab satu-satu' jika user hanya mengajukan satu pertanyaan di pesan terakhir!
   - Jika pertanyaan terakhir tidak berkaitan dengan pertanyaan sebelumnya (misal: tanya owner, tanya resep, atau tanya hal baru), abaikan total topik sebelumnya dan jawab HANYA topik saat ini."""

    @staticmethod
    async def execute_tool(
        session: AsyncSession,
        outlet_id: int,
        tool_name: str,
        arguments: Dict[str, Any]
    ) -> Any:
        logger.info(f"Eksekusi Tool '{tool_name}' dengan argumen: {arguments}")
        if tool_name == "get_sales_overview":
            res = await SalesAnalyticsEngine.get_sales_overview(
                session, outlet_id, arguments["start_date"], arguments["end_date"]
            )
            return res.model_dump()
        elif tool_name == "get_top_products":
            limit = arguments.get("limit", 5)
            sort_by = arguments.get("sort_by", "qty")
            res = await SalesAnalyticsEngine.get_top_products(
                session, outlet_id, arguments["start_date"], arguments["end_date"], limit, sort_by
            )
            return [item.model_dump() for item in res]
        elif tool_name == "get_slow_moving_products":
            limit = arguments.get("limit", 5)
            res = await SalesAnalyticsEngine.get_slow_moving_products(
                session, outlet_id, arguments["start_date"], arguments["end_date"], limit
            )
            return [item.model_dump() for item in res]
        elif tool_name == "get_peak_hours":
            res = await SalesAnalyticsEngine.get_peak_hours(
                session, outlet_id, arguments["date"]
            )
            return [item.model_dump() for item in res]
        elif tool_name == "compare_sales_period":
            res = await SalesAnalyticsEngine.compare_sales_period(
                session,
                outlet_id,
                arguments["period_a_start"],
                arguments["period_a_end"],
                arguments["period_b_start"],
                arguments["period_b_end"]
            )
            return res.model_dump()
        elif tool_name == "get_payment_method_stats":
            res = await SalesAnalyticsEngine.get_payment_method_stats(
                session, outlet_id, arguments["start_date"], arguments["end_date"]
            )
            return [item.model_dump() for item in res]
        elif tool_name == "get_category_contribution":
            res = await SalesAnalyticsEngine.get_category_contribution(
                session, outlet_id, arguments["start_date"], arguments["end_date"]
            )
            return [item.model_dump() for item in res]
        elif tool_name == "get_day_of_week_analysis":
            res = await SalesAnalyticsEngine.get_day_of_week_analysis(
                session, outlet_id, arguments["start_date"], arguments["end_date"]
            )
            return res.model_dump()
        else:
            raise ValueError(f"Tool tidak dikenal: {tool_name}")

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
                    .order_by(ChatMessage.created_at.desc())
                    .limit(6)
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

        # Jika tidak ada API key yang terkonfigurasi, gunakan Local Intelligent Agent Engine
        if not providers:
            logger.info("Tidak ada API Key LLM aktif. Menggunakan Local Fallback Agent Engine.")
            resp = await cls._fallback_local_agent(
                session=session,
                user_message=user_message,
                outlet_id=outlet_id,
                conversation_id=resolved_conv_id,
                history_messages=history_messages
            )
            await cls.save_turn(
                session=session,
                chat_session=chat_session,
                user_message=user_message,
                assistant_reply=resp.reply,
                tool_records=resp.tool_details
            )
            return resp

        # Susun riwayat percakapan (System Prompt -> Riwayat Tertanda Tegas -> Pesan Terbaru)
        base_messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt}
        ]
        # Batasi riwayat hanya ke 2 turn terakhir (maks 4 pesan) agar tidak mencemari memori
        active_history = history_messages[-4:] if len(history_messages) > 4 else history_messages
        for hist in active_history:
            content = hist.content or ""
            if hist.role == "user":
                # Tandai dengan jelas bahwa ini adalah pertanyaan lama yang sudah selesai
                base_messages.append({
                    "role": "user",
                    "content": f"[Pertanyaan Sesi Sebelumnya - SUDAH SELESAI DIJAWAB]: {content}"
                })
            elif hist.role == "assistant":
                # Bersihkan sapaan ganda dan potong ke inti agar tidak menjadi contoh buruk (few-shot contagion)
                clean = content.split("\n\n")[0] if "\n\n" in content else content
                for bad_phrase in [
                    "Dua pertanyaan sekaligus ya, saya jawab satu-satu:",
                    "Dua pertanyaan sekaligus saya jawab ya!",
                    "Dua hal ini saya jawab satu-satu:",
                    "dua pertanyaan sekaligus"
                ]:
                    clean = clean.replace(bad_phrase, "")
                clean = clean.strip()[:150]
                base_messages.append({
                    "role": "assistant",
                    "content": f"[Sudah selesai dijawab oleh asisten: {clean}]"
                })

        # HANYA pesan user terakhir inilah yang AKTIF dan WAJIB DIJAWAB!
        base_messages.append({"role": "user", "content": user_message})

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
                # 1. Panggilan Pertama ke LLM
                response = await client.chat.completions.create(
                    model=model,
                    messages=messages,
                    tools=AGENT_TOOLS,
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

        # Jika seluruh provider eksternal gagal, beralih ke Local Deterministic Engine
        logger.error("Seluruh LLM provider eksternal gagal dihubungi. Menggunakan Local Fallback Agent Engine.")
        resp = await cls._fallback_local_agent(
            session=session,
            user_message=user_message,
            outlet_id=outlet_id,
            conversation_id=resolved_conv_id,
            history_messages=history_messages
        )
        await cls.save_turn(
            session=session,
            chat_session=chat_session,
            user_message=user_message,
            assistant_reply=resp.reply,
            tool_records=resp.tool_details
        )
        return resp

    @classmethod
    async def _fallback_local_agent(
        cls,
        session: AsyncSession,
        user_message: str,
        outlet_id: int,
        conversation_id: Optional[str] = None,
        history_messages: Optional[List[ChatMessage]] = None
    ) -> AgentChatResponse:
        """Deterministic NLP router dengan kesadaran konteks riwayat percakapan."""
        msg_lower = user_message.lower()
        today = datetime.now().date()
        today_str = today.strftime("%Y-%m-%d")
        yesterday_str = (today - timedelta(days=1)).strftime("%Y-%m-%d")
        seven_days_ago_str = (today - timedelta(days=7)).strftime("%Y-%m-%d")

        # Cek tool terakhir yang dieksekusi pada giliran sebelumnya jika ada
        last_tool_executed = None
        if history_messages:
            for h in reversed(history_messages):
                if h.role == "assistant" and h.tool_calls:
                    try:
                        tc = json.loads(h.tool_calls)
                        if isinstance(tc, list) and len(tc) > 0:
                            last_tool_executed = tc[0].get("tool_name")
                            break
                    except Exception:
                        pass

        # Deteksi periode
        start_d = seven_days_ago_str
        end_d = today_str
        if "hari ini" in msg_lower:
            start_d = today_str
            end_d = today_str
        elif "kemarin" in msg_lower:
            start_d = yesterday_str
            end_d = yesterday_str
        elif "minggu ini" in msg_lower or "7 hari" in msg_lower:
            start_d = seven_days_ago_str
            end_d = today_str

        tools_executed = []
        tool_details = []

        # 1. Intent Top Products
        if any(w in msg_lower for w in ["laku", "laris", "menu", "terlaris", "top", "produk"]) or (
            last_tool_executed == "get_top_products" and ("bagaimana" in msg_lower or "kalau" in msg_lower or "kemarin" in msg_lower or "hari ini" in msg_lower)
        ):
            tools_executed.append("get_top_products")
            items = await cls.execute_tool(session, outlet_id, "get_top_products", {
                "start_date": start_d, "end_date": end_d, "limit": 5, "sort_by": "qty"
            })
            tool_details.append(ToolExecutionRecord(tool_name="get_top_products", arguments={"start_date": start_d, "end_date": end_d}, output=items))

            if items:
                reply = f"Halo Juragan! ☕ Berikut 5 menu paling laris periode {start_d} s/d {end_d}:\n\n"
                for idx, it in enumerate(items, 1):
                    reply += f"{idx}. *{it['product_name']}* — {it['quantity_sold']} terjual (Rp{it['total_revenue']:,.0f})\n"
                reply += f"\n💡 *Saran JuraganAI*: Menu #{items[0]['product_name']} adalah bintang Anda! Pastikan stok bahan bakunya selalu siap dan pertimbangkan promo bundling dengan cemilan di jam ramai."
            else:
                reply = f"Halo Juragan! Belum ada data penjualan tercatat untuk periode {start_d} s/d {end_d}. Silakan impor data transaksi POS terlebih dahulu ya."

            return AgentChatResponse(reply=reply, tools_executed=tools_executed, tool_details=tool_details, conversation_id=conversation_id, provider_used="Local Deterministic Engine", model_used="Deterministic NLP")

        # 2. Intent Peak Hours
        elif any(w in msg_lower for w in ["jam", "ramai", "peak", "sepi"]) or (
            last_tool_executed == "get_peak_hours" and ("bagaimana" in msg_lower or "kalau" in msg_lower or "kemarin" in msg_lower or "hari ini" in msg_lower)
        ):
            tools_executed.append("get_peak_hours")
            peaks = await cls.execute_tool(session, outlet_id, "get_peak_hours", {"date": start_d})
            tool_details.append(ToolExecutionRecord(tool_name="get_peak_hours", arguments={"date": start_d}, output=peaks))

            top_peaks = [p for p in peaks if p["transaction_count"] > 0][:3]
            if top_peaks:
                reply = f"Halo Juragan! ⏰ Analisis jam paling ramai pada tanggal {start_d}:\n\n"
                for p in top_peaks:
                    reply += f"• *{p['hour_label']}*: {p['transaction_count']} transaksi (Rp{p['total_revenue']:,.0f})\n"
                reply += f"\n💡 *Saran JuraganAI*: Puncak transaksi ada di jam *{top_peaks[0]['hour_label']}*. Pastikan staf kasir dan barista/dapur siap melayani di jam tersebut agar antrean tidak menumpuk."
            else:
                reply = f"Halo Juragan! Belum ada transaksi yang tercatat pada tanggal {start_d}."

            return AgentChatResponse(reply=reply, tools_executed=tools_executed, tool_details=tool_details, conversation_id=conversation_id, provider_used="Local Deterministic Engine", model_used="Deterministic NLP")

        # 3. Intent Compare Periods
        elif any(w in msg_lower for w in ["banding", "vs", "pertumbuhan", "tren"]):
            p_a_start, p_a_end = (today - timedelta(days=6)).strftime("%Y-%m-%d"), today_str
            p_b_start, p_b_end = (today - timedelta(days=13)).strftime("%Y-%m-%d"), (today - timedelta(days=7)).strftime("%Y-%m-%d")
            tools_executed.append("compare_sales_period")
            comp = await cls.execute_tool(session, outlet_id, "compare_sales_period", {
                "period_a_start": p_a_start, "period_a_end": p_a_end,
                "period_b_start": p_b_start, "period_b_end": p_b_end
            })
            tool_details.append(ToolExecutionRecord(tool_name="compare_sales_period", arguments={}, output=comp))

            reply = f"Halo Juragan! 📊 Perbandingan penjualan (7 Hari Ini vs 7 Hari Sebelumnya):\n\n"
            reply += f"• *7 Hari Terakhir*: Rp{comp['period_a_gmv']:,.0f} ({comp['period_a_orders']} transaksi)\n"
            reply += f"• *7 Hari Sebelumnya*: Rp{comp['period_b_gmv']:,.0f} ({comp['period_b_orders']} transaksi)\n"
            reply += f"• *Pertumbuhan Omzet*: {comp['gmv_growth_percentage']:+.1f}%\n"
            reply += f"• *Tren*: *{comp['trend']}*\n\n"
            reply += f"💡 *Saran JuraganAI*: Tren penjualan sedang {comp['trend'].lower()}. Pertahankan konsistensi rasa dan coba berikan promo diskon tipis di jam sepi untuk mendongkrak transaksi!"
            return AgentChatResponse(reply=reply, tools_executed=tools_executed, tool_details=tool_details, conversation_id=conversation_id, provider_used="Local Deterministic Engine", model_used="Deterministic NLP")

        # 4. Intent Category Breakdown
        elif any(w in msg_lower for w in ["kategori", "category"]) or (
            last_tool_executed == "get_category_contribution" and ("bagaimana" in msg_lower or "kalau" in msg_lower)
        ):
            tools_executed.append("get_category_contribution")
            cats = await cls.execute_tool(session, outlet_id, "get_category_contribution", {
                "start_date": start_d, "end_date": end_d
            })
            tool_details.append(ToolExecutionRecord(tool_name="get_category_contribution", arguments={}, output=cats))
            reply = f"Halo Juragan! 🥧 Kontribusi Penjualan per Kategori ({start_d} s/d {end_d}):\n\n"
            for c in cats:
                reply += f"• *{c['category_name']}*: {c['quantity_sold']} terjual ({c['percentage_of_revenue']}% omzet - Rp{c['total_revenue']:,.0f})\n"
            reply += f"\n💡 *Saran JuraganAI*: Kategori teratas menyumbang porsi pendapatan terbesar. Coba buat menu paket combo lintas kategori (misal: Kopi + Pastry) untuk mendongkrak omzet!"
            return AgentChatResponse(reply=reply, tools_executed=tools_executed, tool_details=tool_details, conversation_id=conversation_id, provider_used="Local Deterministic Engine", model_used="Deterministic NLP")

        # 5. Intent Weekend vs Weekday
        elif any(w in msg_lower for w in ["weekend", "akhir pekan", "hari kerja", "weekday"]):
            tools_executed.append("get_day_of_week_analysis")
            dow = await cls.execute_tool(session, outlet_id, "get_day_of_week_analysis", {
                "start_date": start_d, "end_date": end_d
            })
            tool_details.append(ToolExecutionRecord(tool_name="get_day_of_week_analysis", arguments={}, output=dow))
            reply = f"Halo Juragan! 📅 Analisis Hari Kerja vs Akhir Pekan:\n\n"
            reply += f"• *Rata-rata Hari Kerja*: Rp{dow['weekday_avg_daily_gmv']:,.0f} / hari ({dow['weekday_total_orders']} nota)\n"
            reply += f"• *Rata-rata Akhir Pekan*: Rp{dow['weekend_avg_daily_gmv']:,.0f} / hari ({dow['weekend_total_orders']} nota)\n"
            reply += f"• *Selisih (Weekend Lift)*: {dow['weekend_lift_percentage']:+.1f}%\n\n"
            reply += f"💡 *Insight JuraganAI*: {dow['insight']}"
            return AgentChatResponse(reply=reply, tools_executed=tools_executed, tool_details=tool_details, conversation_id=conversation_id, provider_used="Local Deterministic Engine", model_used="Deterministic NLP")

        # 6. Default: Sales Overview
        else:
            tools_executed.append("get_sales_overview")
            ov = await cls.execute_tool(session, outlet_id, "get_sales_overview", {
                "start_date": start_d, "end_date": end_d
            })
            tool_details.append(ToolExecutionRecord(tool_name="get_sales_overview", arguments={"start_date": start_d, "end_date": end_d}, output=ov))

            reply = f"Halo Juragan! 📊 Ringkasan Penjualan Periode ({start_d} s/d {end_d}):\n\n"
            reply += f"💰 *Total Omzet (GMV)*: Rp{ov['total_gmv']:,.0f}\n"
            reply += f"🧾 *Jumlah Transaksi*: {ov['total_transactions']} struk\n"
            reply += f"🏷️ *Rata-rata Belanja (AOV)*: Rp{ov['average_order_value']:,.0f} / nota\n"
            reply += f"📦 *Total Item Terjual*: {ov['total_items_sold']} pcs\n\n"
            reply += f"💡 *Saran JuraganAI*: Untuk meningkatkan nilai belanja rata-rata (AOV), latih kasir untuk melakukan *upselling* (misal: 'Mau sekalian tambah pastry atau upsize kopinya, Kak?')."
            return AgentChatResponse(reply=reply, tools_executed=tools_executed, tool_details=tool_details, conversation_id=conversation_id, provider_used="Local Deterministic Engine", model_used="Deterministic NLP")
