"""
schoolX_bot/bot/services/rag_client.py — Асинхронный HTTP-клиент для взаимодействия с RAG API.
Бот выступает исключительно в роли тонкого клиента (I/O).
В теле запроса передаются только chat_id, user_id, message_id, query и session_id.
Любые параметры генерации (модели, температура, токены, промпты, top_k) ЗАПРЕЩЕНЫ.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional
import httpx

logger = logging.getLogger("RAGApiClient")


# ==============================================================================
# Исключения клиентского слоя
# ==============================================================================

class RAGClientError(Exception):
    """Базовое исключение RAG-клиента."""
    pass


class RAGConnectionError(RAGClientError):
    """Ошибка подключения к RAG API (сервис недоступен или 503)."""
    pass


class RAGTimeoutError(RAGClientError):
    """Превышено время ожидания ответа от RAG API."""
    pass


class RAGServerError(RAGClientError):
    """Внутренняя ошибка генерации на стороне RAG API (500)."""
    pass


class RAGAuthError(RAGClientError):
    """Ошибка авторизации по API-ключу (401/403)."""
    pass


# ==============================================================================
# Структуры данных ответа
# ==============================================================================

@dataclass
class SourceItem:
    source: str
    page: Optional[Any] = None
    quote: Optional[str] = None


@dataclass
class RAGResponseData:
    answer: str
    sources: List[SourceItem] = field(default_factory=list)
    session_id: str = ""
    confidence: float = 0.0
    latency_ms: float = 0.0
    is_zero_retrieval: bool = False


@dataclass
class RAGStreamEvent:
    delta: str = ""
    done: bool = False
    session_id: str = ""
    sources: List[SourceItem] = field(default_factory=list)
    confidence: float = 0.0
    latency_ms: float = 0.0


# ==============================================================================
# HTTP Клиент RAG API
# ==============================================================================

class RAGApiClient:
    """Асинхронный HTTP-клиент для RAG API."""

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        api_key: Optional[str] = None,
        timeout: float = 60.0
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key.strip() if api_key else None
        self.timeout = timeout

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "SchoolX-Telegram-Bot/2.0"
        }
        if self.api_key:
            headers["X-API-Key"] = self.api_key
            headers["Authorization"] = f"Bearer {self.api_key}"

        self._client: Optional[httpx.AsyncClient] = None
        self._headers = headers

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=self._headers,
                timeout=httpx.Timeout(self.timeout, connect=10.0),
                trust_env=False
            )
        return self._client

    async def close(self) -> None:
        """Закрытие HTTP-сессии при завершении работы бота."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            logger.info("RAGApiClient HTTP session closed.")

    async def check_health(self) -> Dict[str, Any]:
        """Проверка доступности RAG-сервиса."""
        client = self._get_client()
        try:
            resp = await client.get("/api/v1/health")
            if resp.status_code == 200:
                return resp.json()
            return {"status": "degraded", "code": resp.status_code}
        except httpx.ConnectError as ce:
            raise RAGConnectionError(f"Не удалось подключиться к RAG API ({self.base_url}): {ce}") from ce
        except httpx.TimeoutException as te:
            raise RAGTimeoutError(f"Таймаут проверки здоровья RAG API: {te}") from te
        except Exception as e:
            raise RAGClientError(f"Ошибка health check: {e}") from e

    async def health_check(self) -> Dict[str, Any]:
        """Алиас для check_health."""
        try:
            return await self.check_health()
        except Exception:
            return {"status": "unhealthy"}

    async def send_message(
        self,
        chat_id: int | str,
        user_id: int | str,
        query: str,
        message_id: Optional[int | str] = None,
        session_id: Optional[str] = None
    ) -> RAGResponseData:
        """Отправка сообщения в RAG API без стриминга.
        
        СТРОГОЕ ОГРАНИЧЕНИЕ: В теле запроса отправляются ТОЛЬКО идентификаторы и текст запроса.
        """
        client = self._get_client()
        payload = {
            "chat_id": str(chat_id),
            "user_id": str(user_id),
            "message_id": str(message_id) if message_id else None,
            "query": query.strip(),
            "session_id": str(session_id) if session_id else None
        }

        try:
            resp = await client.post("/api/v1/telegram/message", json=payload)
        except httpx.ConnectError as ce:
            logger.error(f"Failed to connect to RAG API at {self.base_url}: {ce}")
            raise RAGConnectionError("Сервер базы знаний временно недоступен.") from ce
        except httpx.TimeoutException as te:
            logger.warning(f"RAG API request timed out after {self.timeout}s: {te}")
            raise RAGTimeoutError("Превышено время ожидания ответа базы знаний.") from te
        except Exception as exc:
            logger.exception(f"Unexpected connection error to RAG API: {exc}")
            raise RAGConnectionError(f"Сбой соединения с RAG API: {exc}") from exc

        if resp.status_code == 200:
            data = resp.json()
            sources = [
                SourceItem(
                    source=item.get("source", "Документ"),
                    page=item.get("page"),
                    quote=item.get("quote")
                )
                for item in data.get("sources", [])
            ]
            return RAGResponseData(
                answer=data.get("answer", ""),
                sources=sources,
                session_id=data.get("session_id", ""),
                confidence=float(data.get("confidence", 0.0)),
                latency_ms=float(data.get("latency_ms", 0.0)),
                is_zero_retrieval=bool(data.get("is_zero_retrieval", False))
            )

        if resp.status_code in (401, 403):
            logger.error(f"RAG API authentication error (HTTP {resp.status_code}): {resp.text}")
            raise RAGAuthError("Ошибка авторизации при обращении к RAG API.")

        if resp.status_code == 503:
            logger.error(f"RAG API 503 Service Unavailable: {resp.text}")
            raise RAGConnectionError("Сервис базы знаний временно перегружен или недоступен.")

        if resp.status_code >= 500:
            logger.error(f"RAG API server error (HTTP {resp.status_code}): {resp.text}")
            raise RAGServerError("Внутренняя ошибка генерации ответа в базе знаний.")

        logger.error(f"Unexpected HTTP {resp.status_code} from RAG API: {resp.text}")
        raise RAGClientError(f"Ошибка API (код {resp.status_code}).")

    async def stream_message(
        self,
        chat_id: int | str,
        user_id: int | str,
        query: str,
        message_id: Optional[int | str] = None,
        session_id: Optional[str] = None
    ) -> AsyncIterator[RAGStreamEvent]:
        """Потоковая отправка запроса в RAG API через Server-Sent Events (SSE).
        
        СТРОГОЕ ОГРАНИЧЕНИЕ: В теле запроса отправляются ТОЛЬКО идентификаторы и текст запроса.
        """
        client = self._get_client()
        payload = {
            "chat_id": str(chat_id),
            "user_id": str(user_id),
            "message_id": str(message_id) if message_id else None,
            "query": query.strip(),
            "session_id": str(session_id) if session_id else None
        }

        try:
            async with client.stream(
                "POST",
                "/api/v1/telegram/message/stream",
                json=payload,
                headers={"Accept": "text/event-stream"}
            ) as stream_resp:

                if stream_resp.status_code in (401, 403):
                    raise RAGAuthError("Ошибка авторизации при стриминге RAG API.")

                if stream_resp.status_code == 503:
                    raise RAGConnectionError("Сервис базы знаний временно перегружен.")

                if stream_resp.status_code >= 500:
                    raise RAGServerError("Внутренняя ошибка генерации в базе знаний.")

                if stream_resp.status_code != 200:
                    raise RAGClientError(f"Ошибка стриминга RAG API (код {stream_resp.status_code}).")

                buffer = ""
                async for chunk in stream_resp.aiter_text():
                    buffer += chunk
                    while "\n\n" in buffer:
                        event_block, buffer = buffer.split("\n\n", 1)
                        for line in event_block.splitlines():
                            line = line.strip()
                            if line.startswith("data:"):
                                data_str = line[len("data:"):].strip()
                                if not data_str:
                                    continue
                                try:
                                    event_dict = json.loads(data_str)
                                    is_done = bool(event_dict.get("done", False))
                                    delta = event_dict.get("delta", "")
                                    sess_id = event_dict.get("session_id", "")
                                    conf = float(event_dict.get("confidence", 0.0))
                                    lat = float(event_dict.get("latency_ms", 0.0))
                                    raw_sources = event_dict.get("sources", [])
                                    sources = [
                                        SourceItem(
                                            source=s.get("source", "Документ"),
                                            page=s.get("page"),
                                            quote=s.get("quote")
                                        )
                                        for s in raw_sources
                                    ]
                                    yield RAGStreamEvent(
                                        delta=delta,
                                        done=is_done,
                                        session_id=sess_id,
                                        sources=sources,
                                        confidence=conf,
                                        latency_ms=lat
                                    )
                                except json.JSONDecodeError:
                                    pass

        except httpx.ConnectError as ce:
            logger.error(f"Stream connect error to RAG API: {ce}")
            raise RAGConnectionError("Не удалось подключиться к сервису базы знаний.") from ce
        except httpx.TimeoutException as te:
            logger.warning(f"Stream timeout from RAG API: {te}")
            raise RAGTimeoutError("Превышено время ожидания ответа от базы знаний.") from te

    async def reset_session(self, chat_id: int | str) -> bool:
        """Сброс контекста беседы для конкретного chat_id на стороне RAG-приложения."""
        client = self._get_client()
        chat_id_str = str(chat_id)

        try:
            resp = await client.post(f"/api/v1/chat/sessions/{chat_id_str}/reset")
            if resp.status_code == 200:
                logger.info(f"Context successfully reset for chat_id={chat_id_str}")
                return True
            logger.warning(f"Reset context returned HTTP {resp.status_code}: {resp.text}")
            return False
        except Exception as e:
            logger.exception(f"Error resetting session for chat_id={chat_id_str}: {e}")
            raise RAGClientError(f"Не удалось сбросить контекст сессии: {e}") from e
