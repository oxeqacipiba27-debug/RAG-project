import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from bot.config import Settings, UserRAGConfig, cfg, load_user_rag_configs_from_file
from bot.services.ingest import DocumentIngestionService
from bot.services.rag_service import RAGService
from bot.services.vector_db import VectorDBService


class TestUserRAGConfig(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.settings = Settings(
            BOT_TOKEN="123456789:test",
            ADMIN_IDS={111},
            RAG_TOP_K=4,
            LLM_TEMPERATURE=0.2,
            LLM_MAX_TOKENS=1500,
            LM_STUDIO_MODEL="default-model",
            CHUNK_SIZE=500,
            CHUNK_OVERLAP=50
        )

    def test_default_config_fallback(self):
        """Проверка, что для неизвестного пользователя отдаются общие настройки по умолчанию."""
        user_cfg = self.settings.get_user_rag_config(99999)
        self.assertEqual(user_cfg.top_k, 4)
        self.assertEqual(user_cfg.temperature, 0.2)
        self.assertEqual(user_cfg.max_tokens, 1500)
        self.assertEqual(user_cfg.model, "default-model")
        self.assertEqual(user_cfg.chunk_size, 500)
        self.assertEqual(user_cfg.chunk_overlap, 50)
        self.assertEqual(user_cfg.system_prompt, self.settings.DEFAULT_SYSTEM_PROMPT)

    def test_set_and_get_user_rag_config(self):
        """Проверка переопределения отдельных параметров для конкретного пользователя."""
        self.settings.set_user_rag_config(
            user_id=12345,
            user_config={
                "top_k": 7,
                "temperature": 0.05,
                "model": "custom-user-model",
                "system_prompt": "Индивидуальный промпт"
            }
        )

        user_cfg = self.settings.get_user_rag_config(12345)
        # Переопределенные параметры
        self.assertEqual(user_cfg.top_k, 7)
        self.assertEqual(user_cfg.temperature, 0.05)
        self.assertEqual(user_cfg.model, "custom-user-model")
        self.assertEqual(user_cfg.system_prompt, "Индивидуальный промпт")
        # Непереопределенные параметры должны остаться дефолтными
        self.assertEqual(user_cfg.max_tokens, 1500)
        self.assertEqual(user_cfg.chunk_size, 500)
        self.assertEqual(user_cfg.chunk_overlap, 50)

        # Другой пользователь не должен затронуться
        other_cfg = self.settings.get_user_rag_config(67890)
        self.assertEqual(other_cfg.top_k, 4)
        self.assertEqual(other_cfg.model, "default-model")

    def test_parse_from_json_string(self):
        """Проверка парсинга настроек пользователей из JSON-строки (как в .env)."""
        json_data = json.dumps({
            "1001": {"top_k": 6, "temperature": 0.8},
            "1002": {"model": "qwen-special", "max_tokens": 3000}
        })

        test_settings = Settings(
            BOT_TOKEN="123456789:test",
            USER_RAG_CONFIGS=json_data
        )

        cfg_1001 = test_settings.get_user_rag_config(1001)
        self.assertEqual(cfg_1001.top_k, 6)
        self.assertEqual(cfg_1001.temperature, 0.8)
        self.assertEqual(cfg_1001.max_tokens, test_settings.LLM_MAX_TOKENS)

        cfg_1002 = test_settings.get_user_rag_config(1002)
        self.assertEqual(cfg_1002.model, "qwen-special")
        self.assertEqual(cfg_1002.max_tokens, 3000)
        self.assertEqual(cfg_1002.top_k, test_settings.RAG_TOP_K)

    def test_load_from_cfg_file(self):
        """Проверка загрузки настроек пользователей из INI/CFG файла."""
        cfg_content = """
[5001]
top_k = 8
temperature = 0.15
max_tokens = 2500
model = llama-3-8b
system_prompt = Вы супер-эксперт.
chunk_size = 350
chunk_overlap = 35

[5002]
top_k = 2
temperature = 0.9
"""
        with tempfile.NamedTemporaryFile("w", suffix=".cfg", delete=False, encoding="utf-8") as f:
            f.write(cfg_content)
            temp_cfg_path = Path(f.name)

        try:
            configs = load_user_rag_configs_from_file(temp_cfg_path)
            self.assertIn(5001, configs)
            self.assertIn(5002, configs)

            cfg_5001 = configs[5001]
            self.assertEqual(cfg_5001.top_k, 8)
            self.assertEqual(cfg_5001.temperature, 0.15)
            self.assertEqual(cfg_5001.max_tokens, 2500)
            self.assertEqual(cfg_5001.model, "llama-3-8b")
            self.assertEqual(cfg_5001.system_prompt, "Вы супер-эксперт.")
            self.assertEqual(cfg_5001.chunk_size, 350)
            self.assertEqual(cfg_5001.chunk_overlap, 35)

            # Проверка загрузки через Settings(USER_RAG_CONFIG_FILE=...)
            file_settings = Settings(
                BOT_TOKEN="123456789:test",
                USER_RAG_CONFIG_FILE=temp_cfg_path
            )
            eff_cfg_5001 = file_settings.get_user_rag_config(5001)
            self.assertEqual(eff_cfg_5001.top_k, 8)
            self.assertEqual(eff_cfg_5001.model, "llama-3-8b")
            self.assertEqual(eff_cfg_5001.chunk_size, 350)

            eff_cfg_5002 = file_settings.get_user_rag_config(5002)
            self.assertEqual(eff_cfg_5002.top_k, 2)
            self.assertEqual(eff_cfg_5002.temperature, 0.9)
            self.assertEqual(eff_cfg_5002.model, file_settings.LM_STUDIO_MODEL)
        finally:
            if temp_cfg_path.exists():
                temp_cfg_path.unlink()

    def test_load_from_json_file(self):
        """Проверка загрузки настроек пользователей из JSON файла."""
        json_content = {
            "7001": {
                "top_k": 10,
                "temperature": 0.0,
                "model": "deepseek-coder"
            }
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(json_content, f)
            temp_json_path = Path(f.name)

        try:
            configs = load_user_rag_configs_from_file(temp_json_path)
            self.assertIn(7001, configs)
            self.assertEqual(configs[7001].top_k, 10)
            self.assertEqual(configs[7001].temperature, 0.0)
            self.assertEqual(configs[7001].model, "deepseek-coder")
        finally:
            if temp_json_path.exists():
                temp_json_path.unlink()

    async def test_rag_service_uses_user_config(self):
        """Проверка применения индивидуальных настроек при вызове RAGService.generate_answer."""
        user_id = 8888
        self.settings.set_user_rag_config(
            user_id=user_id,
            user_config={
                "top_k": 9,
                "temperature": 0.42,
                "max_tokens": 777,
                "model": "custom-instruct-model",
                "system_prompt": "Пользовательский системный промпт"
            }
        )

        mock_vector_db = MagicMock(spec=VectorDBService)
        fake_chunks = [
            {
                "content": "Содержимое документа для теста",
                "metadata": {"source": "manual.pdf", "page": "12"},
                "distance": 0.1
            }
        ]
        mock_vector_db.similarity_search = AsyncMock(return_value=fake_chunks)

        mock_openai_client = MagicMock()
        mock_chat = MagicMock()
        mock_completions = MagicMock()

        fake_choice = MagicMock()
        fake_choice.message.content = "Ответ на основе manual.pdf"
        fake_response = MagicMock()
        fake_response.choices = [fake_choice]

        mock_completions.create = AsyncMock(return_value=fake_response)
        mock_chat.completions = mock_completions
        mock_openai_client.chat = mock_chat

        rag_service = RAGService(
            settings=self.settings,
            vector_db=mock_vector_db,
            openai_client=mock_openai_client
        )

        full_ans, sources_str, chunks = await rag_service.generate_answer(
            user_id=user_id,
            query="Каковы правила?"
        )

        # 1. Проверяем, что поиск чанков вызвался с k=9 (из конфига пользователя, а не дефолтным 4)
        mock_vector_db.similarity_search.assert_awaited_once_with(
            user_id=user_id,
            query="Каковы правила?",
            k=9
        )

        # 2. Проверяем, что запрос к модели был отправлен с параметрами пользователя
        mock_completions.create.assert_awaited_once()
        create_kwargs = mock_completions.create.await_args.kwargs
        self.assertEqual(create_kwargs["model"], "custom-instruct-model")
        self.assertEqual(create_kwargs["temperature"], 0.42)
        self.assertEqual(create_kwargs["max_tokens"], 777)
        self.assertEqual(create_kwargs["messages"][0]["content"], "Пользовательский системный промпт")

        # 3. Проверяем правильность ответа и источников
        self.assertIn("Ответ на основе manual.pdf", full_ans)
        self.assertIn("manual.pdf (стр./раздел: 12)", sources_str)

    async def test_document_ingestion_with_user_chunk_size(self):
        """Проверка, что DocumentIngestionService использует индивидуальный chunk_size."""
        ingest_service = DocumentIngestionService(default_chunk_size=500, default_overlap=50)

        sample_text = (
            "Первое предложение текста. Второе предложение текста. "
            "Третье предложение текста для наполнения объема чанка. "
            "Четвертое предложение текста. Пятое предложение текста."
        )

        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(sample_text)
            temp_txt = Path(f.name)

        mock_vector_db = MagicMock(spec=VectorDBService)
        mock_vector_db.add_documents = AsyncMock(return_value=5)

        try:
            # Вызываем с очень маленьким chunk_size=10
            await ingest_service.ingest_file(
                user_id=999,
                file_path=temp_txt,
                original_filename="test.txt",
                vector_db=mock_vector_db,
                chunk_size=10,
                chunk_overlap=2
            )

            mock_vector_db.add_documents.assert_awaited_once()
            call_docs = mock_vector_db.add_documents.await_args.kwargs["documents"]
            # При маленьком chunk_size документ разбивается на большее количество чанков
            self.assertGreater(len(call_docs), 1)
        finally:
            if temp_txt.exists():
                temp_txt.unlink()


if __name__ == "__main__":
    unittest.main()
