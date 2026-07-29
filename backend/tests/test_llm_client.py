"""Unit tests for llm_client — mock responses, retry logic, connection management."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.core.llm_client import (
    LLMClient,
    LLMResponseTruncatedError,
    _mock_chapter,
    _mock_outline,
    _mock_response,
    _mock_worldview_extraction,
)

# ─── Mock response tests ────────────────────────────────────


class TestMockResponse:
    def test_worldview_extraction_mock(self):
        messages = [
            {"role": "system", "content": "请从以下文本中提取世界观要素"},
            {"role": "user", "content": "这是一个修仙世界的设定文本"},
        ]
        result = _mock_response(messages)
        assert "```json" in result
        assert "characters" in result
        assert "geography" in result

    def test_outline_mock(self):
        messages = [
            {"role": "system", "content": "请生成故事大纲 outline"},
            {"role": "user", "content": "主角是林远"},
        ]
        result = _mock_response(messages)
        assert "```json" in result
        assert "story_arc" in result
        assert "chapters" in result

    def test_chapter_mock(self):
        # Test with worldview elements in the prompt
        user_msg = """请写作第1章。

【章节信息】
标题：觉醒
内容概述：主角发现自身力量

【本章需要揭示的世界观要素】
  - 林远（角色）: 天生灵觉的少年
    · 性格: 坚韧

【故事上下文】
【前情回顾】
  （本章为第一章）"""
        result = _mock_chapter(user_msg)
        assert "林远" in result
        assert len(result) > 100
        assert "开发模式" in result

    def test_chapter_mock_no_elements(self):
        # Test with no worldview elements (fallback)
        user_msg = "请写作第1章。标题：测试"
        result = _mock_chapter(user_msg)
        assert len(result) > 10
        assert "开发模式" in result

    def test_default_mock(self):
        messages = [
            {"role": "system", "content": "其他系统提示"},
            {"role": "user", "content": "用户请求内容"},
        ]
        result = _mock_response(messages)
        assert "[开发模式]" in result

    def test_mock_worldview_extraction_returns_json(self):
        result = _mock_worldview_extraction("任意文本")
        data = json.loads(result.replace("```json\n", "").replace("\n```", ""))
        assert "characters" in data
        assert "geography" in data
        assert "factions" in data
        assert "power_system" in data

    def test_mock_outline_returns_valid_json(self):
        result = _mock_outline("请为全部5章生成大纲", "任意文本")
        data = json.loads(result.replace("```json\n", "").replace("\n```", ""))
        assert "story_arc" in data
        assert len(data["chapters"]) == 5

    def test_mock_outline_dynamic_chapter_count(self):
        """_mock_outline should generate the number of chapters specified in the system prompt."""
        result = _mock_outline("请为全部10章生成大纲", "用户消息")
        data = json.loads(result.replace("```json\n", "").replace("\n```", ""))
        assert len(data["chapters"]) == 10

    def test_mock_outline_defaults_to_5_without_match(self):
        """_mock_outline should default to 5 chapters when system prompt has no chapter count."""
        result = _mock_outline("没有章节数信息的提示", "用户消息")
        data = json.loads(result.replace("```json\n", "").replace("\n```", ""))
        assert len(data["chapters"]) == 5

    def test_mock_chapter_has_content(self):
        result = _mock_chapter("任意文本")
        assert len(result) > 50
        assert "开发模式" in result


# ─── LLMClient configuration tests ───────────────────────────


class TestLLMClientConfig:
    def test_is_configured_false_without_key(self):
        """Without API key, is_configured should be False."""
        client = LLMClient()
        with patch("app.core.llm_client.load_settings") as mock_load:
            mock_load.return_value = {
                "api_key": "",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o",
                "temperature": 0.8,
                "max_tokens": 4096,
            }
            assert client.is_configured is False

    def test_is_configured_true_with_key(self):
        """With API key, is_configured should be True."""
        client = LLMClient()
        with patch("app.core.llm_client.load_settings") as mock_load:
            mock_load.return_value = {
                "api_key": "sk-test-key",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o",
                "temperature": 0.8,
                "max_tokens": 4096,
            }
            assert client.is_configured is True

    def test_headers_contain_auth(self):
        client = LLMClient()
        with patch("app.core.llm_client.load_settings") as mock_load:
            mock_load.return_value = {
                "api_key": "sk-test-key",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o",
                "temperature": 0.8,
                "max_tokens": 4096,
            }
            headers = client._headers()
            assert headers["Authorization"] == "Bearer sk-test-key"
            assert headers["Content-Type"] == "application/json"

    def test_reload_reads_all_settings(self):
        client = LLMClient()
        with patch("app.core.llm_client.load_settings") as mock_load:
            mock_load.return_value = {
                "api_key": "sk-test",
                "base_url": "https://custom.api.com/v1",
                "model": "gpt-4-turbo",
                "temperature": 0.5,
                "max_tokens": 2048,
            }
            client._reload()
            assert client.api_key == "sk-test"
            assert client.base_url == "https://custom.api.com/v1"
            assert client.model == "gpt-4-turbo"
            assert client.temperature == 0.5
            assert client.max_tokens == 2048


# ─── Chat (non-streaming) tests ──────────────────────────────


class TestLLMClientChat:
    @pytest.mark.asyncio
    async def test_chat_returns_mock_without_key(self):
        """Without API key, chat returns mock response."""
        client = LLMClient()
        with patch("app.core.llm_client.load_settings") as mock_load:
            mock_load.return_value = {
                "api_key": "",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o",
                "temperature": 0.8,
                "max_tokens": 4096,
            }
            result = await client.chat(
                [
                    {"role": "system", "content": "请生成章节"},
                    {"role": "user", "content": "第一章"},
                ]
            )
            assert isinstance(result, str)
            assert len(result) > 0

    @pytest.mark.asyncio
    async def test_chat_success_with_mock_http(self):
        """With API key and mocked HTTP, chat returns response content."""
        client = LLMClient()
        with patch("app.core.llm_client.load_settings") as mock_load:
            mock_load.return_value = {
                "api_key": "sk-test",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o",
                "temperature": 0.8,
                "max_tokens": 4096,
            }

            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "choices": [{"message": {"content": "Hello from LLM"}}]
            }

            mock_http_client = AsyncMock()
            mock_http_client.post = AsyncMock(return_value=mock_resp)
            mock_http_client.is_closed = False

            client._client = mock_http_client
            result = await client.chat([{"role": "user", "content": "Hi"}])
            assert result == "Hello from LLM"

    @pytest.mark.asyncio
    async def test_chat_rejects_truncated_response(self):
        client = LLMClient()
        with patch("app.core.llm_client.load_settings") as mock_load:
            mock_load.return_value = {
                "api_key": "sk-test",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o",
                "temperature": 0.8,
                "max_tokens": 4096,
            }
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": "incomplete"},
                    }
                ]
            }
            mock_http_client = AsyncMock()
            mock_http_client.post = AsyncMock(return_value=mock_resp)
            mock_http_client.is_closed = False
            client._client = mock_http_client

            with pytest.raises(LLMResponseTruncatedError):
                await client.chat([{"role": "user", "content": "Hi"}])

    @pytest.mark.asyncio
    async def test_chat_retries_on_429(self):
        """Chat should retry on HTTP 429 and eventually succeed."""
        client = LLMClient()
        with (
            patch("app.core.llm_client.load_settings") as mock_load,
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            mock_load.return_value = {
                "api_key": "sk-test",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o",
                "temperature": 0.8,
                "max_tokens": 4096,
            }

            retry_resp = MagicMock()
            retry_resp.status_code = 429
            retry_resp.json.return_value = {}

            success_resp = MagicMock()
            success_resp.status_code = 200
            success_resp.json.return_value = {
                "choices": [{"message": {"content": "Success after retry"}}]
            }

            mock_http_client = AsyncMock()
            mock_http_client.post = AsyncMock(side_effect=[retry_resp, success_resp])
            mock_http_client.is_closed = False

            client._client = mock_http_client
            result = await client.chat([{"role": "user", "content": "Hi"}])
            assert result == "Success after retry"
            assert mock_http_client.post.call_count == 2

    @pytest.mark.asyncio
    async def test_chat_raises_on_non_retryable_error(self):
        """Chat should raise RuntimeError on HTTP 400 (non-retryable)."""
        client = LLMClient()
        with patch("app.core.llm_client.load_settings") as mock_load:
            mock_load.return_value = {
                "api_key": "sk-test",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o",
                "temperature": 0.8,
                "max_tokens": 4096,
            }

            error_resp = MagicMock()
            error_resp.status_code = 400
            error_resp.json.return_value = {"error": {"message": "Bad request"}}
            error_resp.text = '{"error": {"message": "Bad request"}}'

            mock_http_client = AsyncMock()
            mock_http_client.post = AsyncMock(return_value=error_resp)
            mock_http_client.is_closed = False

            client._client = mock_http_client
            with pytest.raises(RuntimeError, match="LLM API"):
                await client.chat([{"role": "user", "content": "Hi"}])

    @pytest.mark.asyncio
    async def test_chat_exhausts_retries_on_500(self):
        """Chat should raise after max retries on persistent 500."""
        client = LLMClient()
        with (
            patch("app.core.llm_client.load_settings") as mock_load,
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            mock_load.return_value = {
                "api_key": "sk-test",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o",
                "temperature": 0.8,
                "max_tokens": 4096,
            }

            error_resp = MagicMock()
            error_resp.status_code = 500
            error_resp.json.return_value = {
                "error": {"message": "Internal server error"}
            }
            error_resp.text = '{"error": {"message": "Internal server error"}}'

            mock_http_client = AsyncMock()
            mock_http_client.post = AsyncMock(return_value=error_resp)
            mock_http_client.is_closed = False

            client._client = mock_http_client
            with pytest.raises(RuntimeError, match="LLM API"):
                await client.chat([{"role": "user", "content": "Hi"}])
            # Should have tried _MAX_RETRIES + 1 times (initial + 3 retries = 4)
            assert mock_http_client.post.call_count == 4

    @pytest.mark.asyncio
    async def test_chat_retries_on_timeout(self):
        """Chat should retry on TimeoutException."""
        client = LLMClient()
        with (
            patch("app.core.llm_client.load_settings") as mock_load,
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            mock_load.return_value = {
                "api_key": "sk-test",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o",
                "temperature": 0.8,
                "max_tokens": 4096,
            }

            success_resp = MagicMock()
            success_resp.status_code = 200
            success_resp.json.return_value = {
                "choices": [{"message": {"content": "Recovered"}}]
            }

            mock_http_client = AsyncMock()
            mock_http_client.post = AsyncMock(
                side_effect=[
                    httpx.TimeoutException("timeout"),
                    success_resp,
                ]
            )
            mock_http_client.is_closed = False

            client._client = mock_http_client
            result = await client.chat([{"role": "user", "content": "Hi"}])
            assert result == "Recovered"

    @pytest.mark.asyncio
    async def test_chat_retries_on_connect_error(self):
        """Chat should retry on ConnectError."""
        client = LLMClient()
        with (
            patch("app.core.llm_client.load_settings") as mock_load,
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            mock_load.return_value = {
                "api_key": "sk-test",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o",
                "temperature": 0.8,
                "max_tokens": 4096,
            }

            success_resp = MagicMock()
            success_resp.status_code = 200
            success_resp.json.return_value = {
                "choices": [{"message": {"content": "Connected"}}]
            }

            mock_http_client = AsyncMock()
            mock_http_client.post = AsyncMock(
                side_effect=[
                    httpx.ConnectError("connection refused"),
                    success_resp,
                ]
            )
            mock_http_client.is_closed = False

            client._client = mock_http_client
            result = await client.chat([{"role": "user", "content": "Hi"}])
            assert result == "Connected"


# ─── Chat stream tests ──────────────────────────────────────


class TestLLMClientChatStream:
    @pytest.mark.asyncio
    async def test_chat_stream_returns_mock_without_key(self):
        """Without API key, chat_stream yields mock chunks."""
        client = LLMClient()
        with patch("app.core.llm_client.load_settings") as mock_load:
            mock_load.return_value = {
                "api_key": "",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o",
                "temperature": 0.8,
                "max_tokens": 4096,
            }
            chunks = []
            async for chunk in client.chat_stream(
                [{"role": "user", "content": "生成章节"}]
            ):
                chunks.append(chunk)
            assert len(chunks) > 0
            full = "".join(chunks)
            assert len(full) > 0

    @pytest.mark.asyncio
    async def test_chat_stream_parses_sse_chunks(self):
        """chat_stream should parse SSE data lines and yield content."""
        client = LLMClient()
        with patch("app.core.llm_client.load_settings") as mock_load:
            mock_load.return_value = {
                "api_key": "sk-test",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o",
                "temperature": 0.8,
                "max_tokens": 4096,
            }

            sse_lines = [
                'data: {"choices": [{"delta": {"content": "Hello"}}]}',
                'data: {"choices": [{"delta": {"content": " World"}}]}',
                "data: [DONE]",
            ]

            # Create a mock async context manager for stream
            mock_stream_cm = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.aiter_lines = MagicMock(return_value=AsyncIterableMock(sse_lines))

            async def _stream(*args, **kwargs):
                return mock_resp

            mock_stream_cm.__aenter__ = _stream
            mock_stream_cm.__aexit__ = AsyncMock(return_value=None)

            mock_http_client = AsyncMock()
            mock_http_client.stream = MagicMock(return_value=mock_stream_cm)
            mock_http_client.is_closed = False

            client._client = mock_http_client
            chunks = []
            async for chunk in client.chat_stream([{"role": "user", "content": "Hi"}]):
                chunks.append(chunk)
            assert chunks == ["Hello", " World"]

    @pytest.mark.asyncio
    async def test_chat_stream_rejects_truncated_response(self):
        client = LLMClient()
        with patch("app.core.llm_client.load_settings") as mock_load:
            mock_load.return_value = {
                "api_key": "sk-test",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o",
                "temperature": 0.8,
                "max_tokens": 4096,
            }
            sse_lines = [
                'data: {"choices": [{"delta": {"content": "partial"}}]}',
                'data: {"choices": [{"delta": {}, "finish_reason": "length"}]}',
                "data: [DONE]",
            ]
            mock_stream_cm = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.aiter_lines = MagicMock(return_value=AsyncIterableMock(sse_lines))

            async def _stream(*args, **kwargs):
                return mock_resp

            mock_stream_cm.__aenter__ = _stream
            mock_stream_cm.__aexit__ = AsyncMock(return_value=None)
            mock_http_client = AsyncMock()
            mock_http_client.stream = MagicMock(return_value=mock_stream_cm)
            mock_http_client.is_closed = False
            client._client = mock_http_client

            chunks = []
            with pytest.raises(LLMResponseTruncatedError):
                async for chunk in client.chat_stream(
                    [{"role": "user", "content": "Hi"}]
                ):
                    chunks.append(chunk)
            assert chunks == ["partial"]


class AsyncIterableMock:
    """Helper to mock async line iteration."""

    def __init__(self, lines):
        self._lines = lines
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._lines):
            raise StopAsyncIteration
        line = self._lines[self._index]
        self._index += 1
        return line


# ─── Connection management tests ─────────────────────────────


class TestLLMClientConnection:
    @pytest.mark.asyncio
    async def test_get_client_creates_persistent_client(self):
        client = LLMClient()
        client._client = None
        c = await client._get_client()
        assert isinstance(c, httpx.AsyncClient)
        assert not c.is_closed
        # Second call should return same instance
        c2 = await client._get_client()
        assert c is c2
        await c.aclose()

    @pytest.mark.asyncio
    async def test_close_clears_client(self):
        client = LLMClient()
        await client._get_client()
        await client.close()
        assert client._client is None

    @pytest.mark.asyncio
    async def test_close_when_already_closed(self):
        """close() should be safe to call when no client exists."""
        client = LLMClient()
        client._client = None
        await client.close()  # should not raise

    @pytest.mark.asyncio
    async def test_get_client_recreates_after_close(self):
        """After close, _get_client should create a new client."""
        client = LLMClient()
        c1 = await client._get_client()
        await client.close()
        c2 = await client._get_client()
        assert c1 is not c2
        assert not c2.is_closed
        await c2.aclose()


# ─── test_connection tests ────────────────────────────────────


class TestLLMClientTestConnection:
    @pytest.mark.asyncio
    async def test_test_connection_without_key(self):
        client = LLMClient()
        with patch("app.core.llm_client.load_settings") as mock_load:
            mock_load.return_value = {
                "api_key": "",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o",
                "temperature": 0.8,
                "max_tokens": 4096,
            }
            result = await client.test_connection()
            assert result["success"] is False
            assert "API Key" in result["error"]

    @pytest.mark.asyncio
    async def test_test_connection_success(self):
        client = LLMClient()
        with patch("app.core.llm_client.load_settings") as mock_load:
            mock_load.return_value = {
                "api_key": "sk-test",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o",
                "temperature": 0.8,
                "max_tokens": 4096,
            }

            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "choices": [{"message": {"content": "连接成功"}}]
            }

            mock_http_client = AsyncMock()
            mock_http_client.post = AsyncMock(return_value=mock_resp)
            mock_http_client.is_closed = False

            client._client = mock_http_client
            result = await client.test_connection()
            assert result["success"] is True
            assert result["reply"] == "连接成功"
            assert result["model"] == "gpt-4o"

    @pytest.mark.asyncio
    async def test_test_connection_http_error(self):
        client = LLMClient()
        with patch("app.core.llm_client.load_settings") as mock_load:
            mock_load.return_value = {
                "api_key": "sk-test",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o",
                "temperature": 0.8,
                "max_tokens": 4096,
            }

            mock_resp = MagicMock()
            mock_resp.status_code = 401
            mock_resp.json.return_value = {"error": {"message": "Invalid key"}}
            mock_resp.text = '{"error": {"message": "Invalid key"}}'

            mock_http_client = AsyncMock()
            mock_http_client.post = AsyncMock(return_value=mock_resp)
            mock_http_client.is_closed = False

            client._client = mock_http_client
            result = await client.test_connection()
            assert result["success"] is False
            assert "401" in result["error"]

    @pytest.mark.asyncio
    async def test_test_connection_connect_error(self):
        client = LLMClient()
        with patch("app.core.llm_client.load_settings") as mock_load:
            mock_load.return_value = {
                "api_key": "sk-test",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o",
                "temperature": 0.8,
                "max_tokens": 4096,
            }

            mock_http_client = AsyncMock()
            mock_http_client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
            mock_http_client.is_closed = False

            client._client = mock_http_client
            result = await client.test_connection()
            assert result["success"] is False
            assert "无法连接" in result["error"]

    @pytest.mark.asyncio
    async def test_test_connection_timeout(self):
        client = LLMClient()
        with patch("app.core.llm_client.load_settings") as mock_load:
            mock_load.return_value = {
                "api_key": "sk-test",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o",
                "temperature": 0.8,
                "max_tokens": 4096,
            }

            mock_http_client = AsyncMock()
            mock_http_client.post = AsyncMock(
                side_effect=httpx.TimeoutException("timeout")
            )
            mock_http_client.is_closed = False

            client._client = mock_http_client
            result = await client.test_connection()
            assert result["success"] is False
            assert "超时" in result["error"]
