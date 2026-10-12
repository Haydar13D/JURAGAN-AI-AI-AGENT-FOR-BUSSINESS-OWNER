import asyncio
from typing import Optional
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import logger
from app.core.database import AsyncSessionLocal
from app.services.deepseek_agent import DeepSeekAgentService
from app.services.pos_importer import POSImporterService
from app.services.daily_reporter import DailyReporterService

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
        # Hapus webhook jika sebelumnya pernah diset agar getUpdates diizinkan oleh Telegram API
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
                        # Conflict (instance lain sedang polling atau webhook aktif)
                        await asyncio.sleep(5)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error pada loop polling Telegram: {e}")
                await asyncio.sleep(3)

    @classmethod
    async def process_update(cls, update: dict):
        """Memproses webhook / incoming message dari Telegram."""
        message = update.get("message")
        if not message:
            return

        chat_id = message.get("chat", {}).get("id")
        text = message.get("text", "")
        document = message.get("document")

        if not chat_id:
            return

        async with AsyncSessionLocal() as session:
            # 1. Kasus Dokumen File (Unggah CSV / XLSX POS)
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

            # 2. Kasus Command Teks
            if text.startswith("/"):
                cmd = text.split()[0].lower()
                if cmd in ["/start", "/help"]:
                    welcome_text = (
                        f"☕ *Selamat Datang di JuraganAI!*\n\n"
                        f"Saya adalah asisten Business Intelligence pribadi untuk usaha F&B Anda.\n\n"
                        f"📌 *Fitur yang dapat Juragan coba:*\n"
                        f"1. Kirim file CSV/XLSX export POS untuk impor data otomatis.\n"
                        f"2. Tanya performa bisnis: _'Berapa omzet kemarin?'_, _'Apa 5 menu terlaris minggu ini?'_, _'Kapan jam paling ramai?'_\n"
                        f"3. Command cepat:\n"
                        f"   • `/omzet` — Cek ringkasan penjualan hari ini\n"
                        f"   • `/laporan` — Dapatkan laporan harian lengkap\n"
                        f"   • `/reset` — Bersihkan memori percakapan sesi ini\n\n"
                        f"Silakan ketik pertanyaan atau upload file transaksi Anda! 🚀"
                    )
                    await cls.send_message(chat_id, welcome_text)
                    return
                elif cmd == "/laporan":
                    report = await DailyReporterService.generate_daily_report_text(session, settings.DEFAULT_OUTLET_ID)
                    await cls.send_message(chat_id, report)
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

            # 3. Chat Bahasa Alami -> Teruskan ke DeepSeek Agent dengan Multi-Turn Memory
            res = await DeepSeekAgentService.chat(
                session=session,
                user_message=text,
                outlet_id=settings.DEFAULT_OUTLET_ID,
                conversation_id=f"tg_{chat_id}"
            )
            await cls.send_message(chat_id, res.reply)

    @classmethod
    async def send_message(cls, chat_id: int, text: str):
        """Mengirim pesan ke Telegram dengan Markdown dan fallback otomatis ke Plain Text."""
        if not settings.TELEGRAM_BOT_TOKEN:
            logger.warning(f"[Mock Telegram Out] Chat {chat_id}: {text}")
            return

        url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(url, json=payload)
                if res.status_code != 200:
                    # Fallback jika terjadi markdown formatting conflict
                    payload.pop("parse_mode", None)
                    res2 = await client.post(url, json=payload)
                    if res2.status_code != 200:
                        logger.error(f"Gagal kirim pesan ke Telegram chat {chat_id}: {res2.text}")
        except Exception as e:
            logger.error(f"Gagal mengirim pesan ke Telegram chat {chat_id}: {e}")

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
