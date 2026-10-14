"""v1 路由聚合"""
from fastapi import APIRouter

from app.api.v1 import admin, admin_inquiries, auth, chat, knowledge, memory, sessions

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(chat.router)
api_router.include_router(sessions.router)
api_router.include_router(memory.router)
api_router.include_router(knowledge.router)
api_router.include_router(admin.router)
api_router.include_router(admin_inquiries.router)
