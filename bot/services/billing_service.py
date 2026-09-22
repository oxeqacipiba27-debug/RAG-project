from typing import Any, Dict, Optional
from loguru import logger

from bot.config import Settings
from bot.services.db_service import DatabaseService


class BillingService:
    """Сервис управления тарифами, расчетом цен и жизненным циклом подписок."""

    def __init__(self, settings: Settings, db_service: DatabaseService):
        self.settings = settings
        self.db_service = db_service

    async def get_pricing_info(self, user_id: Optional[int] = None) -> Dict[str, Any]:
        """Расчет актуальных цен с учетом акции Early-Bird (первые 10 клиентов).
        
        Если клиент входит в число первых 10 или продлевает свой Early-Bird статус,
        ему предоставляется скидка 40%.
        """
        early_count = await self.db_service.get_early_bird_count()
        remaining_slots = max(0, self.settings.EARLY_BIRD_LIMIT - early_count)

        is_user_already_early = False
        if user_id:
            sub = await self.db_service.get_active_subscription(user_id)
            if sub and sub.get("is_early_bird"):
                is_user_already_early = True

        is_promo = is_user_already_early or (remaining_slots > 0)

        if is_promo:
            basic_price = self.settings.PRICE_BASIC_PROMO
            pro_price = self.settings.PRICE_PRO_PROMO
        else:
            basic_price = self.settings.PRICE_BASIC_FULL
            pro_price = self.settings.PRICE_PRO_FULL

        return {
            "is_promo": is_promo,
            "remaining_slots": remaining_slots,
            "early_count": early_count,
            "early_limit": self.settings.EARLY_BIRD_LIMIT,
            "basic_price": basic_price,
            "pro_price": pro_price,
            "basic_full": self.settings.PRICE_BASIC_FULL,
            "pro_full": self.settings.PRICE_PRO_FULL,
            "is_renewal": is_user_already_early,
        }

    async def get_tier_price(self, tier: str, user_id: Optional[int] = None) -> int:
        """Получение точной стоимости тарифа для конкретного пользователя."""
        pricing = await self.get_pricing_info(user_id=user_id)
        if tier.lower() == "pro":
            return pricing["pro_price"]
        return pricing["basic_price"]

    @staticmethod
    def get_tier_name(tier: str) -> str:
        """Человекочитаемое название тарифа."""
        tier_lower = tier.lower()
        if tier_lower == "pro":
            return "«ПРО»"
        return "«Базовый»"

    async def has_active_access(self, user_id: int) -> bool:
        """Проверка права пользователя на выполнение RAG-запросов и загрузку документов.
        
        Администраторы (ADMIN_IDS) имеют пожизненный безусловный доступ (bypass).
        Обычным пользователям требуется активная подписка.
        """
        if user_id in self.settings.ADMIN_IDS:
            return True

        return await self.db_service.is_subscription_active(user_id)
