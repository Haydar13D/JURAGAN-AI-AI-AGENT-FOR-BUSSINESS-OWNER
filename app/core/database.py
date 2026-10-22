from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.core.config import settings
from app.core.logging import logger

connect_args = {}
if settings.DATABASE_URL.startswith("sqlite"):
    connect_args["check_same_thread"] = False

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    connect_args=connect_args,
    future=True
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False
)

class Base(DeclarativeBase):
    pass

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

async def init_db() -> None:
    """Inisialisasi tabel-tabel basis data jika belum ada."""
    logger.info(f"Menginisialisasi basis data menggunakan: {settings.DATABASE_URL.split('@')[-1] if '@' in settings.DATABASE_URL else settings.DATABASE_URL}")
    import app.models  # Pastikan semua model terdaftar di Base.metadata
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Migrasi ringan untuk SQLite jika kolom baru ditambahkan ke model yang sudah ada
        try:
            from sqlalchemy import text
            await conn.execute(text("ALTER TABLE products ADD COLUMN stock INTEGER DEFAULT 100"))
        except Exception:
            pass  # Kolom sudah ada
    logger.info("Inisialisasi skema basis data selesai.")

