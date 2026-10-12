from fastapi import APIRouter
from app.api.v1.health import router as health_router
from app.api.v1.pos_import import router as pos_router
from app.api.v1.analytics import router as analytics_router
from app.api.v1.agent import router as agent_router

api_router = APIRouter(prefix="/v1")

api_router.include_router(health_router)
api_router.include_router(pos_router)
api_router.include_router(analytics_router)
api_router.include_router(agent_router)
