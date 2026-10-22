import os
import asyncio
import json
from typing import Optional, Dict, Any
import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.config import settings
from app.core.logging import logger
from app.core.database import AsyncSessionLocal
from app.services.deepseek_agent import DeepSeekAgentService
from app.services.pos_importer import POSImporterService
from app.services.daily_reporter import DailyReporterService
from app.services.instagram_publisher import InstagramPublisherService
from app.models.campaign import Campaign, CampaignApproval, AgentEvent

class TelegramBotManager:
    _polling_task: Optional[asyncio.Task] = None
    _is_polling: bool = False

    @classmethod
    async def start_polling(cls):
        """Memulai background long-polling runner untuk menerima pesan Telegram tanpa perlu public webhook."""
        if not settings.TELEGRAM_BOT_TOKEN or cls._is_polling:
            return
        cls._is_polling = True
        cls._polling_task = asyncio.create_task(cls._poll_loop())
        logger.info("Telegram Bot Long Polling runner telah aktif.")

    @classmethod
    async def stop_polling(cls):
        """Menghentikan background long-polling runner saat server shutdown."""
        cls._is_polling = False
        if cls._polling_task:
            cls._polling_task.cancel()
            try:
                await cls._polling_task
            except asyncio.CancelledError:
                pass
            cls._polling_task = None
        logger.info("Telegram Bot Long Polling runner telah dimatikan.")

    @classmethod
    async def _poll_loop(cls):
        offset = 0
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/deleteWebhook")
        except Exception as e:
            logger.warning(f"deleteWebhook info: {e}")

        logger.info("Mulai mendengarkan pesan masuk dari Telegram (getUpdates)...")
        while cls._is_polling:
            try:
                url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/getUpdates"
                params = {"offset": offset, "timeout": 5}
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.get(url, params=params)
                    if resp.status_code == 200:
                        data = resp.json()
                        if data.get("ok"):
                            updates = data.get("result", [])
                            for upd in updates:
                                offset = upd["update_id"] + 1
                                try:
                                    await cls.process_update(upd)
                                except Exception as e:
                                    logger.error(f"Error memproses update Telegram #{upd.get('update_id')}: {e}")
                    elif resp.status_code == 409:
                        await asyncio.sleep(5)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error pada loop polling Telegram: {e}")
                await asyncio.sleep(3)

    @classmethod
    async def process_update(cls, update: dict):
        """Memproses webhook / incoming message / callback query dari Telegram."""
        # 1. Kasus Callback Query (Tombol Interaktif Ditekan oleh Owner)
        callback_query = update.get("callback_query")
        if callback_query:
            await cls.process_callback_query(callback_query)
            return

        message = update.get("message")
        if not message:
            return

        chat_id = message.get("chat", {}).get("id")
        text = message.get("text", "")
        document = message.get("document")

        if not chat_id:
            return

        async with AsyncSessionLocal() as session:
            # 2. Kasus Dokumen File (Unggah CSV / XLSX POS)
            if document:
                file_name = document.get("file_name", "pos_data.csv")
                file_id = document.get("file_id")
                if file_name.lower().endswith((".csv", ".xlsx", ".xls")):
                    await cls.send_message(chat_id, f"📥 Menerima file *{file_name}*... Sedang memproses dan mengimpor ke sistem...")
                    try:
                        file_bytes = await cls._download_telegram_file(file_id)
                        if file_bytes:
                            summary = await POSImporterService.import_file(
                                session=session,
                                file_bytes=file_bytes,
                                filename=file_name,
                                outlet_id=settings.DEFAULT_OUTLET_ID
                            )
                            reply = (
                                f"✅ *Import Data POS Berhasil!*\n\n"
                                f"• File: `{summary.filename}`\n"
                                f"• Total Transaksi: *{summary.imported_transactions} nota*\n"
                                f"• Total Menu: *{summary.imported_items} item*\n"
                                f"• Total Omzet Masuk: *Rp{summary.total_revenue_imported:,.0f}*\n\n"
                                f"Sekarang Juragan bisa langsung tanya: _'Berapa omzet hari ini?'_ atau _'Menu apa yang paling laku?'_"
                            )
                            await cls.send_message(chat_id, reply)
                        else:
                            await cls.send_message(chat_id, "❌ Gagal mengunduh file dari Telegram.")
                    except Exception as e:
                        await cls.send_message(chat_id, f"❌ Terjadi kesalahan saat membaca file: {str(e)}")
                else:
                    await cls.send_message(chat_id, "⚠️ Harap kirimkan file dengan format `.csv` atau `.xlsx` hasil ekspor POS.")
                return

            # 3. Kasus Command Teks
            if text.startswith("/"):
                cmd = text.split()[0].lower()
                if cmd in ["/start", "/help"]:
                    welcome_text = (
                        f"☕ *Selamat Datang di JuraganAI!*\n\n"
                        f"Saya adalah asisten operasional & marketing mandiri untuk usaha F&B Anda.\n\n"
                        f"📌 *Fitur Otonom yang Aktif:*\n"
                        f"1. *Laporan Harian Rutin*: Pukul 08:00 & 10:00 WIB.\n"
                        f"2. *Pemeriksaan Peluang & Usulan Promo (Full Automate)*: Pukul 22:00 WIB (setiap tutup kasir).\n"
                        f"3. *1-Click Approval*: Persetujuan kampanye Instagram langsung dari HP Anda.\n\n"
                        f"📌 *Command Cepat:*\n"
                        f"• `/omzet` — Cek ringkasan penjualan hari ini\n"
                        f"• `/laporan` — Dapatkan laporan harian lengkap\n"
                        f"• `/proposal` — Minta agent buatkan proposal promosi sekarang\n"
                        f"• `/reset` — Bersihkan memori percakapan sesi ini\n\n"
                        f"Silakan ketik pertanyaan atau tunggu notifikasi proaktif dari saya! 🚀"
                    )
                    await cls.send_message(chat_id, welcome_text)
                    return
                elif cmd == "/laporan":
                    report = await DailyReporterService.generate_daily_report_text(session, settings.DEFAULT_OUTLET_ID)
                    await cls.send_message(chat_id, report)
                    return
                elif cmd == "/proposal":
                    await cls.send_message(chat_id, "🔍 *Memindai data POS 60 hari untuk mendeteksi peluang promosi...*")
                    from app.services.opportunity_detector import OpportunityDetector
                    from app.services.campaign_planner import CampaignPlannerService
                    candidates = await OpportunityDetector.detect_opportunities(session, outlet_id=2, cooldown_days=0)
                    if not candidates:
                        await cls.send_message(chat_id, "✨ Semua menu saat ini dalam kondisi performa stabil. Tidak ada anomali penurunan drastis.")
                        return
                    top_cand = candidates[0]
                    opp = await OpportunityDetector.create_opportunity_record(session, top_cand)
                    campaign = await CampaignPlannerService.plan_campaign_for_opportunity(session, opp.id)
                    if campaign:
                        ev = top_cand.get("evidence", {})
                        await cls.send_campaign_proposal(chat_id, campaign, ev)
                    return
                elif cmd == "/omzet":
                    res = await DeepSeekAgentService.chat(
                        session=session,
                        user_message="Berapa omzet dan jumlah transaksi hari ini?",
                        outlet_id=settings.DEFAULT_OUTLET_ID,
                        conversation_id=f"tg_{chat_id}"
                    )
                    await cls.send_message(chat_id, res.reply)
                    return
                elif cmd == "/reset":
                    await DeepSeekAgentService.reset_conversation(
                        session=session,
                        conversation_id=f"tg_{chat_id}",
                        outlet_id=settings.DEFAULT_OUTLET_ID
                    )
                    await cls.send_message(chat_id, "🔄 *Memori sesi percakapan berhasil direset.* Silakan mulai topik baru!")
                    return

            # 4. Chat Bahasa Alami -> Teruskan ke DeepSeek Agent dengan Multi-Turn Memory
            res = await DeepSeekAgentService.chat(
                session=session,
                user_message=text,
                outlet_id=settings.DEFAULT_OUTLET_ID,
                conversation_id=f"tg_{chat_id}"
            )
            await cls.send_message(chat_id, res.reply)

    @classmethod
    async def process_callback_query(cls, callback_query: dict):
        """Menangani interaksi tombol inline dari owner di Telegram."""
        cb_id = callback_query.get("id")
        data = callback_query.get("data", "")
        message = callback_query.get("message", {})
        chat_id = message.get("chat", {}).get("id")
        message_id = message.get("message_id")

        if not chat_id or not data:
            return

        async with AsyncSessionLocal() as session:
            # 1. AKSI SETUJUI & PUBLISH
            if data.startswith("approve_camp_"):
                camp_id = int(data.replace("approve_camp_", ""))
                try:
                    pub_res = await InstagramPublisherService.publish_campaign(
                        session=session,
                        campaign_id=camp_id,
                        approved_by="OWNER (Telegram 1-Click)"
                    )
                    post_id = pub_res.get("post_id", "sim_ig_live")
                    await cls.answer_callback_query(cb_id, text="✅ Proposal Disetujui! Menerbitkan ke Instagram...", show_alert=False)

                    # Update inline keyboard agar terkunci (Idempotency)
                    locked_markup = {
                        "inline_keyboard": [
                            [{"text": "✅ TERBIT DI INSTAGRAM (@kopi_kenangan_senja)", "callback_data": "noop"}],
                            [{"text": "📊 Evaluasi Dampak (7 Hari)", "callback_data": f"eval_camp_{camp_id}"}]
                        ]
                    }
                    await cls.edit_message_reply_markup(chat_id, message_id, locked_markup)

                    confirm_msg = (
                        f"🎉 *PROPOSAL RESMI DISETUJUI & TAYANG!*\n\n"
                        f"• Status: *PUBLISHED ke Instagram*\n"
                        f"• Akun: `@kopi_kenangan_senja` (Simulasi)\n"
                        f"• Mock Post ID: `{post_id}`\n\n"
                        f"🚀 *Siklus Closed-Loop Aktif*: Agent akan memantau omzet produk ini dan melaporkan hasil evaluasi 7 hari lagi."
                    )
                    await cls.send_message(chat_id, confirm_msg)
                except Exception as e:
                    logger.error(f"Error approval via Telegram: {e}")
                    await cls.answer_callback_query(cb_id, text=f"Gagal memproses: {str(e)}", show_alert=True)

            # 2. AKSI MINTA REVISI
            elif data.startswith("revise_camp_"):
                camp_id = int(data.replace("revise_camp_", ""))
                stmt = select(Campaign).where(Campaign.id == camp_id)
                res = await session.execute(stmt)
                c = res.scalar_one_or_none()
                if c:
                    c.status = "REVISION_REQUESTED"
                    session.add(c)
                    await session.commit()

                await cls.answer_callback_query(cb_id, text="Permintaan revisi diterima.", show_alert=False)
                locked_markup = {
                    "inline_keyboard": [
                        [{"text": "✏️ STATUS: REVISI DIMINTA", "callback_data": "noop"}]
                    ]
                }
                await cls.edit_message_reply_markup(chat_id, message_id, locked_markup)
                await cls.send_message(
                    chat_id,
                    f"✏️ *Catatan Revisi untuk Campaign #{camp_id}*\n"
                    f"Silakan ketik revisi apa yang Juragan inginkan (contoh: _'Diskon kurangi jadi 10% saja'_)."
                )

            # 3. AKSI TOLAK / ABAIKAN
            elif data.startswith("reject_camp_"):
                camp_id = int(data.replace("reject_camp_", ""))
                stmt = select(Campaign).where(Campaign.id == camp_id)
                res = await session.execute(stmt)
                c = res.scalar_one_or_none()
                if c:
                    c.status = "REJECTED"
                    session.add(c)
                    await session.commit()

                await cls.answer_callback_query(cb_id, text="Proposal kampanye ditolak.")
                locked_markup = {
                    "inline_keyboard": [
                        [{"text": "❌ PROPOSAL DITOLAK / DIABAIKAN", "callback_data": "noop"}]
                    ]
                }
                await cls.edit_message_reply_markup(chat_id, message_id, locked_markup)

            # 4. AKSI EVALUASI HASIL
            elif data.startswith("eval_camp_"):
                camp_id = int(data.replace("eval_camp_", ""))
                from app.services.campaign_evaluator import CampaignEvaluatorService
                eval_res = await CampaignEvaluatorService.evaluate_campaign(session, camp_id)
                await cls.answer_callback_query(cb_id, text="Memuat evaluasi kampanye...")
                msg = (
                    f"📊 *LAPORAN EVALUASI KAMPANYE*\n"
                    f"Produk: *{eval_res['product_name']}*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"• Penjualan Sebelum: *{eval_res['baseline_qty']} cup*\n"
                    f"• Penjualan Sesudah: *{eval_res['evaluation_qty']} cup*\n"
                    f"• Pertumbuhan Qty: *{eval_res['qty_growth_pct']}%*\n"
                    f"• Status: *{eval_res['verdict']}*\n\n"
                    f"💡 *Catatan Agent*:\n_{eval_res['narration']}_"
                )
                await cls.send_message(chat_id, msg)

    @classmethod
    async def send_campaign_proposal(cls, chat_id: int, campaign: Campaign, evidence: Dict[str, Any]):
        """Mengirimkan materi proposal promosi lengkap beserta tombol inline approval ke Telegram owner."""
        disc_val = f"{int(campaign.discount_pct)}%" if campaign.discount_pct else "Non-Diskon"
        promo_price = int(evidence.get("price", 30000) * (1 - (campaign.discount_pct or 0) / 100))

        text_lines = [
            f"🚨 *PELUANG PROMOSI TERDETEKSI (JuraganAI)*",
            f"🏪 *Kopi Kenangan Senja*",
            f"━━━━━━━━━━━━━━━━━━━━━━",
            f"Halo Juragan! ☕ Hasil analisis transaksi kasir:",
            f"• Menu: *{campaign.product_name}*",
            f"• Sinyal: *{evidence.get('rule', 'SHARP_DROP')}* ({evidence.get('drop_qty_pct', 0)}% drop)",
            f"• Estimasi Omzet Hilang: *Rp{evidence.get('lost_revenue', 0):,.0f}*",
            f"• Margin Bersih: *{evidence.get('margin_pct', 0)}%* (Aman untuk promo)",
            f"━━━━━━━━━━━━━━━━━━━━━━",
            f"🎯 *Strategi*: {campaign.objective}",
            f"🏷️ *Rekomendasi*: Diskon *{disc_val}* (Harga promo: Rp{promo_price:,.0f})",
            f"━━━━━━━━━━━━━━━━━━━━━━",
            f"📝 *Draf Caption Instagram*:\n_{campaign.caption[:250]}..._\n",
            f"👉 *Ketik salah satu tombol di bawah untuk memutuskan:*"
        ]
        caption_full = "\n".join(text_lines)

        reply_markup = {
            "inline_keyboard": [
                [{"text": "✅ Setujui & Publish ke IG (Simulasi)", "callback_data": f"approve_camp_{campaign.id}"}],
                [
                    {"text": "✏️ Minta Revisi", "callback_data": f"revise_camp_{campaign.id}"},
                    {"text": "❌ Tolak / Abaikan", "callback_data": f"reject_camp_{campaign.id}"}
                ]
            ]
        }

        # Kirim dokumen/foto flyer jika file fisik tersedia di disk
        flyer_rel = campaign.media_url or ""
        flyer_local = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            flyer_rel.lstrip("/")
        )

        sent = False
        if os.path.exists(flyer_local):
            sent = await cls.send_document_with_markup(chat_id, flyer_local, caption_full, reply_markup)

        if not sent:
            await cls.send_message_with_markup(chat_id, caption_full, reply_markup)

    @classmethod
    async def send_message_with_markup(cls, chat_id: int, text: str, reply_markup: dict) -> bool:
        """Mengirim pesan teks dengan tombol inline keyboard."""
        if not settings.TELEGRAM_BOT_TOKEN:
            logger.warning(f"[Mock Telegram Out with Markup] Chat {chat_id}: {text}")
            return False

        url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "reply_markup": reply_markup
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(url, json=payload)
                if res.status_code != 200:
                    payload.pop("parse_mode", None)
                    res2 = await client.post(url, json=payload)
                    return res2.status_code == 200
                return True
        except Exception as e:
            logger.error(f"Gagal kirim pesan dengan markup ke chat {chat_id}: {e}")
            return False

    @classmethod
    async def send_document_with_markup(cls, chat_id: int, file_path: str, caption: str, reply_markup: dict) -> bool:
        """Mengirim dokumen/gambar lokal beserta tombol inline keyboard."""
        if not settings.TELEGRAM_BOT_TOKEN:
            return False

        url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendDocument"
        try:
            with open(file_path, "rb") as f:
                files = {"document": (os.path.basename(file_path), f.read())}
            data = {
                "chat_id": str(chat_id),
                "caption": caption[:1024],  # Batas maksimal caption Telegram
                "parse_mode": "Markdown",
                "reply_markup": json.dumps(reply_markup)
            }
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.post(url, data=data, files=files)
                return res.status_code == 200
        except Exception as e:
            logger.error(f"Gagal kirim dokumen Telegram ke {chat_id}: {e}")
            return False

    @classmethod
    async def answer_callback_query(cls, callback_query_id: str, text: str = "", show_alert: bool = False):
        if not settings.TELEGRAM_BOT_TOKEN:
            return
        url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/answerCallbackQuery"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(url, json={
                    "callback_query_id": callback_query_id,
                    "text": text,
                    "show_alert": show_alert
                })
        except Exception as e:
            logger.warning(f"Gagal answerCallbackQuery: {e}")

    @classmethod
    async def edit_message_reply_markup(cls, chat_id: int, message_id: int, reply_markup: dict):
        if not settings.TELEGRAM_BOT_TOKEN:
            return
        url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/editMessageReplyMarkup"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(url, json={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "reply_markup": reply_markup
                })
        except Exception as e:
            logger.warning(f"Gagal editMessageReplyMarkup: {e}")

    @classmethod
    async def send_message(cls, chat_id: int, text: str):
        """Mengirim pesan teks ke Telegram dengan fallback Markdown."""
        if not settings.TELEGRAM_BOT_TOKEN:
            logger.warning(f"[Mock Telegram Out] Chat {chat_id}: {text}")
            return

        url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(url, json=payload)
                if res.status_code != 200:
                    payload.pop("parse_mode", None)
                    await client.post(url, json=payload)
        except Exception as e:
            logger.error(f"Gagal kirim pesan ke Telegram chat {chat_id}: {e}")

    @classmethod
    async def _download_telegram_file(cls, file_id: str) -> Optional[bytes]:
        if not settings.TELEGRAM_BOT_TOKEN:
            return None
        get_file_url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/getFile?file_id={file_id}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.get(get_file_url)
            data = res.json()
            if not data.get("ok"):
                return None
            file_path = data["result"]["file_path"]
            download_url = f"https://api.telegram.org/file/bot{settings.TELEGRAM_BOT_TOKEN}/{file_path}"
            dl_res = await client.get(download_url)
            return dl_res.content
