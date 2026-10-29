"""v1 路由聚合"""
from fastapi import APIRouter

from app.api.v1 import (
    admin,
    admin_analytics,
    admin_canned_scripts,
    admin_inquiries,
    admin_integrations,
    admin_rbac,
    admin_router,
    admin_scenes,
    admin_staff,
    admin_takeover,
    admin_tickets,
    admin_traces,
    auth,
    chat,
    knowledge,
    memory,
    public,
    sessions,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(public.router)
api_router.include_router(chat.router)
api_router.include_router(sessions.router)
api_router.include_router(memory.router)
api_router.include_router(knowledge.router)
api_router.include_router(admin.router)
api_router.include_router(admin_scenes.router)
api_router.include_router(admin_staff.router)
api_router.include_router(admin_rbac.router)
api_router.include_router(admin_integrations.router)
api_router.include_router(admin_router.router)
api_router.include_router(admin_inquiries.router)
api_router.include_router(admin_takeover.router)
api_router.include_router(admin_tickets.router)
api_router.include_router(admin_traces.router)
api_router.include_router(admin_analytics.router)
api_router.include_router(admin_canned_scripts.router)
