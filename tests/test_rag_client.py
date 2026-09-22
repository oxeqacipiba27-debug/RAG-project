"""
Unit tests for RAGApiClient in schoolX_bot.
Verifies thin-client HTTP communication, SSE parsing, and typed error handling.
"""

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

from bot.services.rag_client import (
    RAGApiClient,
    RAGAuthError,
    RAGConnectionError,
    RAGResponseData,
    RAGServerError,
    RAGStreamEvent,
    RAGTimeoutError,
    SourceItem,
)


class TestRAGApiClient(unittest.IsolatedAsyncioTestCase):
    """Test suite for RAGApiClient."""

    async def asyncSetUp(self):
        self.client = RAGApiClient(
            base_url="http://localhost:8000",
            api_key="test-api-key",
            timeout=10.0,
        )
        self.client._get_client()

    async def asyncTearDown(self):
        await self.client.close()

    async def test_send_message_success(self):
        """Test successful send_message parses response correctly."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "answer": "Прием документов ведется до 25 июля.",
            "sources": [
                {
                    "source": "admission_rules.pdf",
                    "page": 4,
                    "quote": "Прием документов завершается 25 июля.",
                }
            ],
            "session_id": "sess-uuid-1234",
            "confidence": 0.95,
            "latency_ms": 120.5,
            "is_zero_retrieval": False,
        }

        with patch.object(self.client._client, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            res = await self.client.send_message(
                chat_id=123,
                user_id=456,
                query="До какого числа прием?",
            )

            self.assertIsInstance(res, RAGResponseData)
            self.assertEqual(res.answer, "Прием документов ведется до 25 июля.")
            self.assertEqual(res.session_id, "sess-uuid-1234")
            self.assertEqual(len(res.sources), 1)
            self.assertEqual(res.sources[0].source, "admission_rules.pdf")
            self.assertEqual(res.sources[0].page, 4)

            # Verify that only thin client fields were sent
            mock_post.assert_awaited_once()
            call_kwargs = mock_post.await_args.kwargs
            payload = call_kwargs["json"]
            self.assertEqual(payload["chat_id"], "123")
            self.assertEqual(payload["user_id"], "456")
            self.assertEqual(payload["query"], "До какого числа прием?")
            # Ensure no generation params leaked into request
            for forbidden in ["temperature", "top_k", "top_p", "system_prompt", "model", "chunks"]:
                self.assertNotIn(forbidden, payload)

    async def test_send_message_connection_error(self):
        """Test connection failure raises RAGConnectionError."""
        with patch.object(self.client._client, "post", side_effect=httpx.ConnectError("Connection refused")):
            with self.assertRaises(RAGConnectionError):
                await self.client.send_message(chat_id=1, user_id=1, query="test")

    async def test_send_message_timeout(self):
        """Test request timeout raises RAGTimeoutError."""
        with patch.object(self.client._client, "post", side_effect=httpx.TimeoutException("Read timed out")):
            with self.assertRaises(RAGTimeoutError):
                await self.client.send_message(chat_id=1, user_id=1, query="test")

    async def test_send_message_auth_error(self):
        """Test HTTP 401 raises RAGAuthError."""
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"
        with patch.object(self.client._client, "post", return_value=mock_response):
            with self.assertRaises(RAGAuthError):
                await self.client.send_message(chat_id=1, user_id=1, query="test")

    async def test_send_message_server_error(self):
        """Test HTTP 500 raises RAGServerError."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        with patch.object(self.client._client, "post", return_value=mock_response):
            with self.assertRaises(RAGServerError):
                await self.client.send_message(chat_id=1, user_id=1, query="test")

    async def test_reset_session(self):
        """Test reset_session calls endpoint and returns True on success."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "ok", "message": "Session reset", "chat_id": "123"}

        with patch.object(self.client._client, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            success = await self.client.reset_session(chat_id=123)
            self.assertTrue(success)
            mock_post.assert_awaited_once_with("/api/v1/chat/sessions/123/reset")

    async def test_health_check(self):
        """Test health_check endpoint query."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "healthy",
            "service": "schoolX-rag-api",
            "rag_pipeline_loaded": True,
        }

        with patch.object(self.client._client, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            data = await self.client.health_check()
            self.assertEqual(data["status"], "healthy")
            self.assertTrue(data["rag_pipeline_loaded"])

    async def test_stream_message_sse_parsing(self):
        """Test stream_message correctly parses SSE lines and terminates on done."""
        async def mock_aiter_text():
            yield 'data: {"delta": "Привет, ", "done": false}\n\n'
            yield 'data: {"delta": "мир!", "done": false}\n\n'
            yield 'data: {"done": true, "session_id": "sess-42", "sources": [{"source": "doc.pdf", "page": 2}], "confidence": 0.9}\n\n'

        mock_stream_ctx = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.aiter_text = mock_aiter_text

        # Asynchronous context manager mock
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=None)

        with patch.object(self.client._client, "stream", return_value=mock_stream_ctx):
            events = []
            async for ev in self.client.stream_message(chat_id=1, user_id=1, query="Привет"):
                events.append(ev)

            self.assertEqual(len(events), 3)
            self.assertEqual(events[0].delta, "Привет, ")
            self.assertFalse(events[0].done)
            self.assertEqual(events[1].delta, "мир!")
            self.assertFalse(events[1].done)
            self.assertTrue(events[2].done)
            self.assertEqual(events[2].session_id, "sess-42")
            self.assertEqual(len(events[2].sources), 1)
            self.assertEqual(events[2].sources[0].source, "doc.pdf")


if __name__ == "__main__":
    unittest.main()
