import html
from typing import Any, Dict, List, Optional, Tuple
from loguru import logger
import openai

from bot.config import DEFAULT_SYSTEM_PROMPT, Settings
from bot.services.vector_db import VectorDBService
from bot.utils.formatter import format_telegram_html


class RAGService:
    """Сервис Retrieval-Augmented Generation.
    
    Отвечает за:
    1. Поиск релевантных чанков в персональной ChromaDB коллекции пользователя (k из user cfg или settings).
    2. Формирование строгого системного промпта (персонального или дефолтного).
    3. Отправку запроса в локальный инференс LM Studio через AsyncOpenAI с учетом настроек пользователя.
    4. Оформление списка использованных источников.
    """

    SYSTEM_PROMPT = DEFAULT_SYSTEM_PROMPT

    def __init__(
        self,
        settings: Settings,
        vector_db: VectorDBService,
        openai_client: openai.AsyncOpenAI
    ):
        self.settings = settings
        self.vector_db = vector_db
        self.client = openai_client

    def _format_context(self, chunks: List[Dict[str, Any]]) -> Tuple[str, List[str]]:
        """Сборка контекста из чанков и формирование списка источников."""
        if not chunks:
            return "", []

        context_parts = []
        sources_set = set()

        for chunk in chunks:
            text = chunk.get("content", "").strip()
            meta = chunk.get("metadata", {})
            source = meta.get("source", "Неизвестный документ")
            page = meta.get("page", "-")

            context_parts.append(f"[Документ: {source}, Стр./Раздел: {page}]\n{text}")
            sources_set.add((source, page))

        context_str = "\n\n---\n\n".join(context_parts)
        formatted_sources = [f"{src} (стр./раздел: {pg})" for src, pg in sorted(sources_set)]
        return context_str, formatted_sources

    async def generate_answer(self, user_id: int, query: str) -> Tuple[str, str, List[Dict[str, Any]]]:
        """Генерация ответа на основе персональной базы знаний пользователя с индивидуальными настройками RAG.
        
        Returns:
            Tuple[full_response_text, sources_summary_string, list_of_retrieved_chunks]
        """
        # Получаем эффективную конфигурацию RAG для пользователя (с учетом индивидуальных переопределений)
        user_rag_cfg = self.settings.get_user_rag_config(user_id)

        logger.info(
            f"User {user_id}: RAG query received: '{query[:80]}...' | "
            f"Config: top_k={user_rag_cfg.top_k}, model='{user_rag_cfg.model}', "
            f"temp={user_rag_cfg.temperature}, max_tokens={user_rag_cfg.max_tokens}"
        )

        # 1. Поиск релевантных фрагментов строго в персональной коллекции пользователя
        chunks = await self.vector_db.similarity_search(
            user_id=user_id,
            query=query,
            k=user_rag_cfg.top_k
        )
        
        if not chunks:
            logger.warning(f"User {user_id}: No relevant chunks found in personal VectorDB.")
            return (
                "В базе знаний нет информации по данному вопросу.",
                "",
                []
            )

        context, sources_list = self._format_context(chunks)

        # 2. Формирование пользовательского сообщения с контекстом
        user_prompt = (
            f"Используй следующий контекст для ответа на вопрос.\n\n"
            f"Контекст:\n{context}\n\n"
            f"Вопрос: {query}"
        )

        # 3. Вызов OpenAI-совместимого API LM Studio с индивидуальными параметрами
        try:
            response = await self.client.chat.completions.create(
                model=user_rag_cfg.model,
                messages=[
                    {"role": "system", "content": user_rag_cfg.system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=user_rag_cfg.temperature,
                max_tokens=user_rag_cfg.max_tokens,
            )
            raw_answer = response.choices[0].message.content or ""
            raw_answer = raw_answer.strip()
        except openai.APIConnectionError as e:
            logger.error(f"LM Studio connection error: {e}")
            raise RuntimeError(
                "Ошибка соединения с сервером LM Studio. Убедитесь, что LM Studio запущен на http://localhost:1234."
            ) from e
        except Exception as e:
            logger.error(f"Error calling LM Studio: {e}")
            raise RuntimeError(f"Ошибка инференса модели: {e}") from e

        # 4. Преобразование ответа модели в чистый Telegram HTML без Markdown-артефактов
        formatted_answer = format_telegram_html(raw_answer)

        # 5. Форматирование блока источников
        sources_summary = ", ".join(sources_list)
        if sources_list:
            escaped_sources = [html.escape(s) for s in sources_list]
            sources_block = "\n\n<b>Источники:</b>\n" + "\n".join(f"• <i>{s}</i>" for s in escaped_sources)
            full_response = f"{formatted_answer}{sources_block}"
        else:
            full_response = formatted_answer

        return full_response, sources_summary, chunks
