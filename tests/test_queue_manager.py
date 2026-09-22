import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock
from pathlib import Path
import tempfile
import shutil

from bot.services.db_service import DatabaseService
from bot.services.queue_manager import QueueManager
from bot.services.rag_service import RAGService


class TestQueueManager(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_service = DatabaseService(Path(self.temp_dir) / "test.db")
        await self.db_service.init_db()

        # Мокаем RAGService для тестирования очереди
        self.mock_rag = MagicMock(spec=RAGService)
        
        # Симулируем задержку инференса LLM
        async def fake_generate_answer(user_id: int, query: str):
            await asyncio.sleep(0.1)
            return f"Ответ на '{query}'", "test_source.pdf (стр. 1)", []

        self.mock_rag.generate_answer = AsyncMock(side_effect=fake_generate_answer)

        self.queue_manager = QueueManager(
            rag_service=self.mock_rag,
            db_service=self.db_service
        )

        # Мокаем Bot
        self.mock_bot = MagicMock()
        self.mock_bot.send_message = AsyncMock(return_value=MagicMock(message_id=999))
        self.mock_bot.edit_message_text = AsyncMock()
        self.mock_bot.send_chat_action = AsyncMock()
        self.mock_bot.delete_message = AsyncMock()

        self.queue_manager.start(self.mock_bot)

    async def asyncTearDown(self):
        await self.queue_manager.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_single_concurrency_and_fifo_order(self):
        """Проверка, что несколько параллельных запросов обрабатываются строго по очереди."""
        execution_order = []

        async def worker_probe(query: str, user_id: int):
            ans, log_id, q_id = await self.queue_manager.submit_query(
                user_id=user_id,
                chat_id=user_id,
                query=query,
                bot=self.mock_bot
            )
            execution_order.append(user_id)
            return ans

        # Запускаем 3 запроса практически одновременно
        t1 = asyncio.create_task(worker_probe("Запрос 1", 101))
        await asyncio.sleep(0.01) # Даем первому попасть в обработку
        t2 = asyncio.create_task(worker_probe("Запрос 2", 102))
        t3 = asyncio.create_task(worker_probe("Запрос 3", 103))

        res1, res2, res3 = await asyncio.gather(t1, t2, t3)

        self.assertEqual(res1, "Ответ на 'Запрос 1'")
        self.assertEqual(res2, "Ответ на 'Запрос 2'")
        self.assertEqual(res3, "Ответ на 'Запрос 3'")

        # Порядок завершения должен строго совпадать с порядком поступления
        self.assertEqual(execution_order, [101, 102, 103])

        # Проверяем, что для ожидавших пользователей отправлялись сообщения с очередью
        self.assertGreaterEqual(self.mock_bot.send_message.call_count, 1)

    async def test_queue_status_reporting(self):
        status = self.queue_manager.get_status()
        self.assertIn("queue_length", status)
        self.assertIn("is_busy", status)
        self.assertFalse(status["is_busy"])


if __name__ == "__main__":
    unittest.main()
