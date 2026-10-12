import sys
import uvicorn
from app.core.config import settings

if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        
    print(f"🚀 Memulai JuraganAI di http://localhost:{settings.PORT}")
    print(f"📚 Swagger UI Docs: http://localhost:{settings.PORT}/docs")
    print(f"🔌 Health check: http://localhost:{settings.PORT}/api/v1/health")
    
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG
    )
