import asyncio
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from bot.services.db_service import DatabaseService


class TestDatabaseService(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / "test_bot.db"
        self.db_service = DatabaseService(self.db_path)
        await self.db_service.init_db()

    async def asyncTearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_bootstrap_and_whitelist(self):
        admin_ids = {11111, 22222}
        allowed_ids = {33333}
        await self.db_service.bootstrap_users(admin_ids, allowed_ids)

        self.assertTrue(await self.db_service.is_user_allowed(11111))
        self.assertTrue(await self.db_service.is_user_allowed(22222))
        self.assertTrue(await self.db_service.is_user_allowed(33333))
        self.assertFalse(await self.db_service.is_user_allowed(99999))

    async def test_add_and_ban_user(self):
        user_id = 55555
        self.assertFalse(await self.db_service.is_user_allowed(user_id))

        # Add user
        await self.db_service.add_user(user_id, added_by=11111)
        self.assertTrue(await self.db_service.is_user_allowed(user_id))

        # Ban user
        banned = await self.db_service.ban_user(user_id)
        self.assertTrue(banned)
        self.assertFalse(await self.db_service.is_user_allowed(user_id))

    async def test_query_logs_and_feedback(self):
        log_id = await self.db_service.log_query(
            user_id=123,
            query_text="Что такое SchoolX?",
            response_text="SchoolX — это образовательная платформа.",
            sources="regulations.pdf (стр. 1)"
        )
        self.assertGreater(log_id, 0)

        # Update feedback (like = 1)
        updated = await self.db_service.set_feedback(log_id=log_id, rating=1)
        self.assertTrue(updated)

        stats = await self.db_service.get_stats()
        self.assertEqual(stats["total_queries"], 1)
        self.assertEqual(stats["positive_feedback"], 1)
        self.assertEqual(stats["negative_feedback"], 0)

    async def test_user_documents_crud(self):
        user_id = 77777
        self.assertEqual(await self.db_service.get_total_documents_count(), 0)

        # 1. Запись документа
        doc_id = await self.db_service.record_document(
            user_id=user_id,
            filename="report.docx",
            file_type=".docx",
            file_size=10240,
            chunks_count=5
        )
        self.assertGreater(doc_id, 0)
        self.assertEqual(await self.db_service.get_total_documents_count(), 1)

        # 2. Получение списка документов пользователя
        docs = await self.db_service.get_user_documents(user_id)
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0]["filename"], "report.docx")
        self.assertEqual(docs[0]["file_type"], ".docx")
        self.assertEqual(docs[0]["chunks_count"], 5)

        # 3. Обновление того же документа (ON CONFLICT REPLACE)
        await self.db_service.record_document(
            user_id=user_id,
            filename="report.docx",
            file_type=".docx",
            file_size=12000,
            chunks_count=6
        )
        docs_updated = await self.db_service.get_user_documents(user_id)
        self.assertEqual(len(docs_updated), 1)
        self.assertEqual(docs_updated[0]["chunks_count"], 6)

        # 4. Добавление второго документа (Excel)
        await self.db_service.record_document(
            user_id=user_id,
            filename="budget.xlsx",
            file_type=".xlsx",
            file_size=20480,
            chunks_count=8
        )
        self.assertEqual(await self.db_service.get_total_documents_count(), 2)

        stats = await self.db_service.get_stats()
        self.assertEqual(stats["total_documents"], 2)

        # 5. Удаление одного документа
        deleted = await self.db_service.delete_document(user_id, "report.docx")
        self.assertTrue(deleted)
        docs_after_del = await self.db_service.get_user_documents(user_id)
        self.assertEqual(len(docs_after_del), 1)
        self.assertEqual(docs_after_del[0]["filename"], "budget.xlsx")

        # 6. Полная очистка документов пользователя
        cleared = await self.db_service.clear_user_documents(user_id)
        self.assertEqual(cleared, 1)
        self.assertEqual(len(await self.db_service.get_user_documents(user_id)), 0)


if __name__ == "__main__":
    unittest.main()
