"""SchoolX Handlers Package."""

from bot.handlers.admin import router as admin_router
from bot.handlers.admin_billing import router as admin_billing_router
from bot.handlers.subscription import router as subscription_router
from bot.handlers.user import router as user_router

__all__ = [
    "admin_router",
    "admin_billing_router",
    "subscription_router",
    "user_router",
]
