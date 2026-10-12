from datetime import datetime
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_db
from app.core.config import settings

router = APIRouter()

@router.get("/health", tags=["System"])
async def health_check(db: AsyncSession = Depends(get_db)):
    db_status = "connected"
    try:
        await db.execute(text("SELECT 1"))
    except Exception as e:
        db_status = f"error: {str(e)}"

    return {
        "status": "healthy",
        "app_name": settings.APP_NAME,
        "environment": settings.APP_ENV,
        "database": db_status,
        "deepseek_configured": bool(settings.DEEPSEEK_API_KEY),
        "telegram_configured": bool(settings.TELEGRAM_BOT_TOKEN),
        "server_time": datetime.now().isoformat()
    }
