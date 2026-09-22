import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional, Tuple
from aiogram import Bot
from loguru import logger

from bot.services.db_service import DatabaseService
from bot.services.rag_service import RAGService


@dataclass
class QueueItem:
    """Элемент задачи в очереди к LM Studio."""
    task_id: str
    user_id: int
    chat_id: int
    query: str
    future: asyncio.Future
    queue_message_id: Optional[int] = None
    created_at: float = 0.0


class QueueManager:
    """Контроллер очереди запросов к LM Studio.
    
    ЖЕСТКОЕ ОГРАНИЧЕНИЕ VRAM (NVIDIA RTX 4070 12GB):
    Параллельные запросы вызывают OOM или падение инференс-сервера.
    QueueManager гарантирует строго однопоточную (concurrency=1) обработку запросов
    через FIFO-очередь asyncio.Queue с уведомлением пользователя о его позиции в очереди
    и цикличным запуском статуса typing во время генерации.
    """

    def __init__(self, rag_service: RAGService, db_service: DatabaseService):
        self.rag_service = rag_service
        self.db_service = db_service
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
        self._worker_task = asyncio.create_task(self._worker_loop(), name="LMStudio-Queue-Worker")
        logger.info("QueueManager worker started (concurrency=1).")

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
            # Если воркер уже генерирует ответ или в очереди уже есть задачи
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
            logger.info(
                f"Enqueued query from user {user_id} (task_id={item.task_id}). "
                f"Queue length: {self._queue.qsize()}, busy: {self._is_busy}"
            )

        # Ожидание завершения обработки воркером
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
            logger.debug(f"ChatAction typing failed for chat {chat_id}: {e}")

    async def _worker_loop(self) -> None:
        """Основной цикл фонового воркера (обрабатывает ровно 1 задачу единовременно)."""
        logger.info("Worker loop entered.")
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
                    # Если было отправлено сообщение об очереди, обновляем статус
                    if item.queue_message_id:
                        try:
                            await bot.edit_message_text(
                                chat_id=item.chat_id,
                                message_id=item.queue_message_id,
                                text="⚡ <b>Ваша очередь подошла!</b>\nАнализирую базу знаний и генерирую ответ..."
                            )
                        except Exception as edit_err:
                            logger.debug(f"Could not edit queue message: {edit_err}")

                    # Запускаем анимацию набора текста
                    typing_task = asyncio.create_task(
                        self._typing_loop(bot, item.chat_id),
                        name=f"typing-{item.task_id}"
                    )

                # Выполнение RAG-конвейера (персональная база ChromaDB + LM Studio)
                answer, sources, chunks = await self.rag_service.generate_answer(
                    user_id=item.user_id,
                    query=item.query
                )

                # Фиксация в БД логов
                log_id = await self.db_service.log_query(
                    user_id=item.user_id,
                    query_text=item.query,
                    response_text=answer,
                    sources=sources
                )

                if not item.future.done():
                    item.future.set_result((answer, log_id, item.queue_message_id))

            except Exception as exc:
                logger.exception(f"Error during execution of task {item.task_id}: {exc}")
                if not item.future.done():
                    item.future.set_exception(exc)
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
        """Текущее состояние очереди."""
        return {
            "queue_length": self._queue.qsize(),
            "is_busy": self._is_busy,
            "active_user": self._active_item.user_id if self._active_item else None
        }
