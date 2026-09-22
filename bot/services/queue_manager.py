"""
schoolX_bot/bot/services/queue_manager.py — Менеджер очереди запросов и синхронизации с RAG API.
Реализует:
1. Защиту от перегрузки и строгий порядок выполнения.
2. Фоновый статус 'typing' во время генерации.
3. Потоковый вывод (SSE) с троттлингом редактирования сообщений в Telegram (не чаще 1 раза в 1.2-1.5 сек).
4. Корректное разбиение сообщений длиннее 4000 символов и оформление первоисточников.
5. Защиту от раскрытия деталей технического стека при сбоях (fallback сообщения).
"""

import asyncio
import html
import time
import uuid
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from loguru import logger
from bot.config import Settings
from bot.keyboards.feedback_kb import get_feedback_keyboard
from bot.services.db_service import DatabaseService
from bot.services.rag_client import (
    RAGApiClient,
    RAGAuthError,
    RAGClientError,
    RAGConnectionError,
    RAGServerError,
    RAGTimeoutError,
    SourceItem,
)
from bot.utils.formatter import format_telegram_html, split_message_text, strip_html_tags


@dataclass
class QueueItem:
    """Элемент задачи в очереди запросов."""
    task_id: str
    user_id: int
    chat_id: int
    query: str
    future: asyncio.Future
    queue_message_id: Optional[int] = None
    created_at: float = 0.0


class QueueManager:
    """Контроллер очереди запросов к RAG API с поддержкой троттлинга стриминга."""

    def __init__(
        self,
        rag_client: RAGApiClient,
        db_service: DatabaseService,
        settings: Settings
    ):
        self.rag_client = rag_client
        self.db_service = db_service
        self.settings = settings
        self._queue: asyncio.Queue[QueueItem] = asyncio.Queue()
        self._is_busy: bool = False
        self._active_item: Optional[QueueItem] = None
        self._lock = asyncio.Lock()
        self._worker_task: Optional[asyncio.Task] = None
        self._bot: Optional[Bot] = None
        self._running: bool = False

    def start(self, bot: Bot) -> None:
        """Запуск фонового воркера очереди."""
        self._bot = bot
        self._running = True
        self._worker_task = asyncio.create_task(self._worker_loop(), name="RAG-Queue-Worker")
        logger.info("QueueManager worker started.")

    async def stop(self) -> None:
        """Мягкая остановка воркера очереди."""
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None
        logger.info("QueueManager worker stopped.")

    async def submit_query(
        self,
        user_id: int,
        chat_id: int,
        query: str,
        bot: Bot
    ) -> Tuple[str, int, Optional[int]]:
        """Добавление запроса пользователя в очередь и ожидание результата.
        
        Returns:
            Tuple[full_answer, log_id, queue_message_id]
        """
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        queue_msg_id: Optional[int] = None

        async with self._lock:
            qsize = self._queue.qsize()
            if self._is_busy or qsize > 0:
                position = qsize + 1
                try:
                    queue_msg = await bot.send_message(
                        chat_id=chat_id,
                        text=f"⏳ <b>Запрос принят.</b>\nВы в очереди: <b>позиция {position}</b>. Пожалуйста, подождите..."
                    )
                    queue_msg_id = queue_msg.message_id
                except Exception as e:
                    logger.warning(f"Failed to send queue position message: {e}")

            item = QueueItem(
                task_id=str(uuid.uuid4()),
                user_id=user_id,
                chat_id=chat_id,
                query=query,
                future=future,
                queue_message_id=queue_msg_id,
                created_at=time.time()
            )
            await self._queue.put(item)
            logger.info(f"Enqueued task {item.task_id} for user {user_id}. Queue size: {self._queue.qsize()}")

        return await future

    async def _typing_loop(self, bot: Bot, chat_id: int) -> None:
        """Периодическая отправка Telegram chat action 'typing' (каждые 4 секунды)."""
        try:
            while True:
                await bot.send_chat_action(chat_id=chat_id, action="typing")
                await asyncio.sleep(4.0)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"ChatAction typing error for chat {chat_id}: {e}")

    def _format_sources_block(self, sources: List[SourceItem]) -> str:
        """Формирование блока первоисточников в формате HTML."""
        if not sources:
            return ""

        unique_sources = set()
        for s in sources:
            pg = f", стр. {s.page}" if s.page is not None else ""
            unique_sources.add(f"{s.source}{pg}")

        escaped = [html.escape(item) for item in sorted(unique_sources)]
        return "\n\n<b>📚 Источники:</b>\n" + "\n".join(f"• <i>{item}</i>" for item in escaped)

    async def _execute_streaming(
        self,
        bot: Bot,
        item: QueueItem
    ) -> Tuple[str, str]:
        """Потоковая генерация ответа с троттлингом обновления сообщений в Telegram."""
        target_msg_id = item.queue_message_id
        placeholder_text = "🔍 <i>Поиск в базе знаний и формулирование ответа...</i>"

        if target_msg_id:
            try:
                await bot.edit_message_text(
                    chat_id=item.chat_id,
                    message_id=target_msg_id,
                    text=placeholder_text
                )
            except Exception:
                target_msg_id = None

        if not target_msg_id:
            try:
                init_msg = await bot.send_message(
                    chat_id=item.chat_id,
                    text=placeholder_text
                )
                target_msg_id = init_msg.message_id
            except Exception as e:
                logger.warning(f"Could not send placeholder message: {e}")

        accumulated_text = ""
        last_rendered_text = ""
        last_edit_time = time.time()
        final_sources: List[SourceItem] = []

        stream_gen = self.rag_client.stream_message(
            chat_id=item.chat_id,
            user_id=item.user_id,
            query=item.query
        )

        async for event in stream_gen:
            if event.delta:
                accumulated_text += event.delta

            if event.done:
                final_sources = event.sources
                break

            now = time.time()
            # Троттлинг: обновляем сообщение не чаще 1 раза в 1.3 секунды
            if now - last_edit_time >= 1.3 and accumulated_text.strip() != last_rendered_text.strip():
                display_chunk = format_telegram_html(accumulated_text) + " ▌"
                if target_msg_id and len(display_chunk) < 4000:
                    try:
                        await bot.edit_message_text(
                            chat_id=item.chat_id,
                            message_id=target_msg_id,
                            text=display_chunk
                        )
                        last_rendered_text = accumulated_text
                        last_edit_time = now
                    except TelegramRetryAfter as tra:
                        await asyncio.sleep(tra.retry_after)
                    except TelegramBadRequest:
                        pass
                    except Exception as e:
                        logger.debug(f"Throttled edit failed: {e}")

        # Финализация ответа
        sources_block = self._format_sources_block(final_sources)
        formatted_answer = format_telegram_html(accumulated_text)
        full_response = f"{formatted_answer}{sources_block}" if formatted_answer else "В базе знаний нет информации по данному вопросу."

        # Редактируем финальным текстом
        if target_msg_id:
            chunks = split_message_text(full_response, max_length=4000)
            try:
                await bot.edit_message_text(
                    chat_id=item.chat_id,
                    message_id=target_msg_id,
                    text=chunks[0]
                )
            except TelegramBadRequest:
                await bot.edit_message_text(
                    chat_id=item.chat_id,
                    message_id=target_msg_id,
                    text=strip_html_tags(chunks[0])
                )
            except Exception as e:
                logger.debug(f"Final edit of first chunk failed: {e}")

            # Если ответ длиннее 4000 символов, отправляем оставшиеся части
            for extra_chunk in chunks[1:]:
                try:
                    await bot.send_message(chat_id=item.chat_id, text=extra_chunk)
                except TelegramBadRequest:
                    await bot.send_message(chat_id=item.chat_id, text=strip_html_tags(extra_chunk))

        sources_summary = ", ".join(f"{s.source}" for s in final_sources)
        return full_response, sources_summary, target_msg_id

    async def _execute_non_streaming(
        self,
        bot: Bot,
        item: QueueItem
    ) -> Tuple[str, str]:
        """Непотоковая отправка запроса в RAG API."""
        if item.queue_message_id:
            try:
                await bot.edit_message_text(
                    chat_id=item.chat_id,
                    message_id=item.queue_message_id,
                    text="⚡ <i>Анализирую базу знаний и формулирую ответ...</i>"
                )
            except Exception:
                pass

        resp = await self.rag_client.send_message(
            chat_id=item.chat_id,
            user_id=item.user_id,
            query=item.query
        )

        formatted_answer = format_telegram_html(resp.answer)
        sources_block = self._format_sources_block(resp.sources)
        full_response = f"{formatted_answer}{sources_block}"

        if item.queue_message_id:
            try:
                await bot.delete_message(chat_id=item.chat_id, message_id=item.queue_message_id)
            except Exception:
                pass

        sources_summary = ", ".join(f"{s.source}" for s in resp.sources)
        return full_response, sources_summary

    async def _worker_loop(self) -> None:
        """Основной цикл фонового воркера."""
        logger.info("QueueManager worker loop started.")
        while self._running:
            try:
                item: QueueItem = await self._queue.get()
            except asyncio.CancelledError:
                break

            self._is_busy = True
            self._active_item = item
            logger.info(f"Processing task {item.task_id} for user {item.user_id}...")

            bot = self._bot
            typing_task: Optional[asyncio.Task] = None

            try:
                if bot:
                    typing_task = asyncio.create_task(
                        self._typing_loop(bot, item.chat_id),
                        name=f"typing-{item.task_id}"
                    )

                if self.settings.RAG_STREAM_ENABLED and bot:
                    answer, sources, target_msg_id = await self._execute_streaming(bot, item)
                    log_id = await self.db_service.log_query(
                        user_id=item.user_id,
                        query_text=item.query,
                        response_text=answer,
                        sources=sources
                    )
                    if target_msg_id and log_id > 0:
                        try:
                            await bot.edit_message_reply_markup(
                                chat_id=item.chat_id,
                                message_id=target_msg_id,
                                reply_markup=get_feedback_keyboard(log_id)
                            )
                        except Exception:
                            pass
                    if not item.future.done():
                        item.future.set_result((answer, log_id, None))
                elif bot:
                    answer, sources = await self._execute_non_streaming(bot, item)
                    log_id = await self.db_service.log_query(
                        user_id=item.user_id,
                        query_text=item.query,
                        response_text=answer,
                        sources=sources
                    )
                    if not item.future.done():
                        item.future.set_result((answer, log_id, "NEEDS_SEND"))
                else:
                    resp = await self.rag_client.send_message(
                        chat_id=item.chat_id,
                        user_id=item.user_id,
                        query=item.query
                    )
                    answer = resp.answer
                    sources = ", ".join(s.source for s in resp.sources)
                    log_id = await self.db_service.log_query(
                        user_id=item.user_id,
                        query_text=item.query,
                        response_text=answer,
                        sources=sources
                    )
                    if not item.future.done():
                        item.future.set_result((answer, log_id, "NEEDS_SEND"))

            except RAGConnectionError as e:
                logger.error(f"RAGConnectionError for task {item.task_id}: {e}")
                err_text = (
                    "❌ <b>Сервис базы знаний временно недоступен.</b>\n"
                    "Пожалуйста, повторите попытку через пару минут."
                )
                if not item.future.done():
                    item.future.set_result((err_text, 0, item.queue_message_id))

            except RAGTimeoutError as e:
                logger.warning(f"RAGTimeoutError for task {item.task_id}: {e}")
                err_text = (
                    "⏳ <b>Время ожидания ответа истекло.</b>\n"
                    "Сервис испытывает высокую нагрузку. Пожалуйста, повторите запрос чуть позже."
                )
                if not item.future.done():
                    item.future.set_result((err_text, 0, item.queue_message_id))

            except RAGServerError as e:
                logger.error(f"RAGServerError for task {item.task_id}: {e}")
                err_text = (
                    "⚠️ <b>Не удалось сформировать ответ.</b>\n"
                    "Попробуйте переформулировать ваш вопрос или обратитесь к администратору."
                )
                if not item.future.done():
                    item.future.set_result((err_text, 0, item.queue_message_id))

            except RAGAuthError as e:
                logger.error(f"RAGAuthError for task {item.task_id}: {e}")
                err_text = "🔒 <b>Ошибка авторизации сервиса базы знаний.</b> Пожалуйста, обратитесь к администратору."
                if not item.future.done():
                    item.future.set_result((err_text, 0, item.queue_message_id))

            except Exception as exc:
                logger.exception(f"Unexpected error executing task {item.task_id}: {exc}")
                err_text = (
                    "❌ <b>Произошла ошибка при обработке запроса.</b>\n"
                    "Пожалуйста, повторите попытку позже."
                )
                if not item.future.done():
                    item.future.set_result((err_text, 0, item.queue_message_id))

            finally:
                if typing_task and not typing_task.done():
                    typing_task.cancel()
                    try:
                        await typing_task
                    except asyncio.CancelledError:
                        pass

                self._is_busy = False
                self._active_item = None
                self._queue.task_done()
                logger.info(f"Task {item.task_id} completed.")

    def get_status(self) -> dict:
        return {
            "queue_length": self._queue.qsize(),
            "is_busy": self._is_busy,
            "active_user": self._active_item.user_id if self._active_item else None
        }
