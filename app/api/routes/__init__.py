"""Combined FastAPI router for the complete declared API contract."""

from fastapi import APIRouter

from app.api.routes import account, auth, cats, chat, facts, feedback, moments

router = APIRouter()
router.include_router(auth.router)
router.include_router(cats.router)
router.include_router(chat.router)
router.include_router(facts.router)
router.include_router(moments.router)
router.include_router(feedback.router)
router.include_router(account.router)

__all__ = ["router"]

