"""
schoolX_bot/bot/services/rag_service.py — Адаптер совместимости RAGService поверх RAGApiClient.
Делегирует все запросы в централизованный RAG API без локального инференса и без параметров генерации.
"""

from typing import Any, Dict, List, Optional, Tuple
from loguru import logger

from bot.config import Settings
from bot.services.rag_client import RAGApiClient


class RAGService:
    """Адаптер для обратной совместимости, использующий RAGApiClient.
    
    Вся логика генерации, параметры пайплайна, retrieval и настройки LLM
    централизованно исполняются исключительно на стороне RAG API.
    """

    def __init__(
        self,
        settings: Settings,
        rag_client: Optional[RAGApiClient] = None,
        *args,
        **kwargs
    ):
        self.settings = settings
        self.rag_client = rag_client or RAGApiClient(
            base_url=settings.RAG_API_BASE_URL,
            api_key=settings.RAG_API_KEY,
            timeout=settings.RAG_REQUEST_TIMEOUT
        )

    async def generate_answer(
        self,
        user_id: int,
        query: str,
        chat_id: Optional[int] = None
    ) -> Tuple[str, str, List[Dict[str, Any]]]:
        """Делегирование генерации ответа в RAG API без параметров генерации на стороне бота."""
        eff_chat_id = chat_id if chat_id is not None else user_id
        logger.info(f"User {user_id}: Delegating query to RAG API via RAGApiClient...")

        resp = await self.rag_client.send_message(
            chat_id=eff_chat_id,
            user_id=user_id,
            query=query
        )

        sources_list = [
            f"{s.source}" + (f" (стр. {s.page})" if s.page is not None else "")
            for s in resp.sources
        ]
        sources_summary = ", ".join(sources_list)
        chunks = [
            {"content": s.quote or "", "metadata": {"source": s.source, "page": s.page}}
            for s in resp.sources
        ]

        return resp.answer, sources_summary, chunks
