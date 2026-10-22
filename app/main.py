from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.logging import logger
from app.core.database import init_db
from app.api.v1.router import api_router
from app.services.daily_reporter import init_daily_scheduler
from app.bot.telegram_bot import TelegramBotManager

scheduler = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global scheduler
    logger.info("Memulai aplikasi JuraganAI...")
    # Inisialisasi basis data
    await init_db()
    # Inisialisasi scheduler laporan harian
    try:
        scheduler = init_daily_scheduler()
        scheduler.start()
        logger.info(f"Daily Reporter Scheduler aktif (Jadwal: {settings.DAILY_REPORT_CRON_HOUR:02d}:{settings.DAILY_REPORT_CRON_MINUTE:02d} {settings.TIMEZONE}).")
        logger.info(f"LLM Provider Utama: {settings.LLM_PROVIDER.upper()} | Model: {settings.ATMOROUTER_MODEL} | Turn Isolation Active")
    except Exception as e:
        logger.warning(f"Gagal memulai scheduler: {e}")

    # Inisialisasi Telegram Bot Long Polling jika token tersedia
    if settings.TELEGRAM_BOT_TOKEN:
        try:
            await TelegramBotManager.start_polling()
            logger.info("Telegram Bot Polling runner aktif.")
        except Exception as e:
            logger.warning(f"Gagal memulai Telegram Bot Polling: {e}")

    yield

    if settings.TELEGRAM_BOT_TOKEN:
        try:
            await TelegramBotManager.stop_polling()
        except Exception as e:
            logger.warning(f"Gagal menghentikan Telegram Bot Polling: {e}")

    if scheduler and scheduler.running:
        scheduler.shutdown()
        logger.info("Scheduler dimatikan.")
    logger.info("Aplikasi JuraganAI dihentikan.")

app = FastAPI(
    title=settings.APP_NAME,
    version="2.1.0",
    description="AI Business Intelligence Assistant untuk Pemilik Usaha F&B (Kafe, Resto, Kedai)",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API v1 Router
app.include_router(api_router, prefix="/api")

@app.post("/api/v1/telegram/webhook", tags=["Telegram Bot"])
async def telegram_webhook(request: Request):
    """Endpoint webhook untuk menerima event pesan dari Telegram Bot."""
    try:
        data = await request.json()
        await TelegramBotManager.process_update(data)
        return {"ok": True}
    except Exception as e:
        logger.error(f"Error memproses webhook Telegram: {e}")
        return JSONResponse(status_code=400, content={"error": str(e)})

import os
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "templates", "dashboard.html")

@app.get("/juragan", response_class=HTMLResponse, tags=["General"])
@app.get("/dashboard", response_class=HTMLResponse, tags=["General"])
async def dashboard():
    """Dashboard lama JuraganAI (modul F&B legacy, calon template kedua Kirata)."""
    if os.path.exists(TEMPLATE_PATH):
        with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse("<h1>JuraganAI Dashboard</h1><p>Template dashboard belum ditemukan.</p>")

KIRATA_TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "templates", "kirata_builder.html")

@app.get("/", response_class=HTMLResponse, tags=["Kirata Workflow Builder"])
@app.get("/kirata", response_class=HTMLResponse, tags=["Kirata Workflow Builder"])
async def kirata_builder_ui():
    """Halaman utama: Kirata AI Workflow Builder (template gallery, tool catalog, builder, runs)."""
    if os.path.exists(KIRATA_TEMPLATE_PATH):
        with open(KIRATA_TEMPLATE_PATH, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse("<h1>Kirata AI Workflow Builder</h1><p>Template belum ditemukan.</p>")


@app.get("/api/info", tags=["General"])
async def api_info():
    return {
        "message": "Selamat datang di JuraganAI — AI Business Intelligence Assistant untuk F&B",
        "version": "2.1.0",
        "dashboard_url": "/",
        "docs_url": "/docs",
        "health_check": "/api/v1/health"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=settings.HOST, port=settings.PORT, reload=settings.DEBUG)
