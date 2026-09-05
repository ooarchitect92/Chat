from fastapi import APIRouter

from northstar_api.routers import (
    agents,
    analytics,
    auth,
    chat,
    conversations,
    integrations,
    knowledge,
    leads,
    whatsapp,
    widget,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(agents.router)
api_router.include_router(knowledge.router)
api_router.include_router(conversations.router)
api_router.include_router(leads.router)
api_router.include_router(analytics.router)
api_router.include_router(integrations.router)
api_router.include_router(widget.router)
api_router.include_router(whatsapp.router)
api_router.include_router(chat.router)
