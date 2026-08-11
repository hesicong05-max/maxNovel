"""LLM API client — supports any OpenAI-compatible endpoint.

Uses a persistent httpx.AsyncClient for connection pool reuse.
Includes retry logic for transient failures (429, 500, 502, 503, 504).
"""

import asyncio
import json
import logging
from typing import AsyncGenerator

import httpx

from app.core.settings_store import load_settings

logger = logging.getLogger(__name__)

# Retry configuration
_MAX_RETRIES = 3
_RETRY_STATUS_CODES = {429, 500, 502, 503, 504}
_RETRY_DELAYS = [1, 2, 4]  # seconds, exponential backoff


class LLMResponseTruncatedError(RuntimeError):
    """Raised when the provider stops because the output token limit was reached."""


class LLMSingleCallError(RuntimeError):
    """Safe failure metadata for an at-most-once LLM request."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        outcome_unknown: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.retryable = retryable
        self.outcome_unknown = outcome_unknown


class LLMClient:
    """Async client for OpenAI-compatible chat completions API."""

    def __init__(self):
        self._reload()
        self._client: httpx.AsyncClient | None = None

    def _reload(self):
        """Reload settings from the settings store (called on each request)."""
        s = load_settings()
        self.api_key = s.get("api_key", "")
        self.base_url = s.get("base_url", "https://api.openai.com/v1")
        self.model = s.get("model", "gpt-4o")
        self.max_tokens = s.get("max_tokens", 4096)
        self.temperature = s.get("temperature", 0.8)

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create a persistent httpx.AsyncClient for connection reuse."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=10, read=300, write=10, pool=5),
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            )
        return self._client

    async def close(self):
        """Close the persistent client. Call on app shutdown."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    @property
    def is_configured(self) -> bool:
        self._reload()
        return bool(self.api_key)

    def _headers(self) -> dict[str, str]:
        self._reload()
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def chat_once(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Call the provider exactly once, with no mock response or retry.

        Extraction jobs use this method because retrying an uncertain request can
        duplicate provider work or charges. Error metadata never contains the
        provider response body or user content.
        """

        self._reload()
        if not self.api_key:
            raise LLMSingleCallError(
                "LLM_NOT_CONFIGURED",
                "LLM 尚未配置，未发起提取调用",
            )

        client = await self._get_client()
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
        }
        try:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
            )
        except httpx.TimeoutException as exc:
            raise LLMSingleCallError(
                "LLM_OUTCOME_UNKNOWN",
                "LLM 请求超时，结果状态无法确认",
                outcome_unknown=True,
            ) from exc
        except httpx.TransportError as exc:
            raise LLMSingleCallError(
                "LLM_OUTCOME_UNKNOWN",
                "LLM 连接中断，结果状态无法确认",
                outcome_unknown=True,
            ) from exc

        if response.status_code != 200:
            raise LLMSingleCallError(
                "LLM_REQUEST_REJECTED",
                f"LLM 请求未成功（HTTP {response.status_code}）",
                retryable=response.status_code in _RETRY_STATUS_CODES,
            )

        try:
            data = response.json()
            choice = data["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMSingleCallError(
                "LLM_RESPONSE_INVALID",
                "LLM 返回了无法读取的响应",
            ) from exc
        if choice.get("finish_reason") == "length":
            raise LLMSingleCallError(
                "LLM_RESPONSE_TRUNCATED",
                "LLM 输出不完整，未保存任何候选",
            )
        if not isinstance(content, str):
            raise LLMSingleCallError(
                "LLM_RESPONSE_INVALID",
                "LLM 返回了无法读取的响应",
            )
        return content

    async def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Non-streaming chat completion. Returns the full response text.

        Retries on transient failures (429, 5xx) with exponential backoff.
        """
        self._reload()
        if not self.api_key:
            logger.warning(
                "LLM MOCK MODE: No API key configured — returning mock response. "
                "The generated content will be generic and NOT based on your worldview data. "
                "Configure API key via /settings page or .env file."
            )
            return _mock_response(messages)

        client = await self._get_client()
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
        }

        last_error = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                )
                if resp.status_code in _RETRY_STATUS_CODES and attempt < _MAX_RETRIES:
                    delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                    logger.warning(
                        "LLM API retryable error (HTTP %d), attempt %d/%d, retrying in %ds",
                        resp.status_code,
                        attempt + 1,
                        _MAX_RETRIES,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue

                if resp.status_code != 200:
                    try:
                        err_data = resp.json()
                        err_msg = err_data.get("error", {}).get("message", resp.text)
                    except Exception:
                        err_msg = (
                            resp.text[:200] if resp.text else f"HTTP {resp.status_code}"
                        )
                    raise RuntimeError(
                        f"LLM API 错误 (HTTP {resp.status_code}): {err_msg}"
                    )

                data = resp.json()
                choice = data["choices"][0]
                if choice.get("finish_reason") == "length":
                    raise LLMResponseTruncatedError(
                        "LLM 输出达到 token 上限，内容不完整，请提高最大输出 token 或降低目标字数"
                    )
                return choice["message"]["content"]

            except httpx.TimeoutException:
                last_error = RuntimeError("LLM API 请求超时")
                if attempt < _MAX_RETRIES:
                    delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                    logger.warning(
                        "LLM timeout, attempt %d/%d, retrying in %ds",
                        attempt + 1,
                        _MAX_RETRIES,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise last_error
            except httpx.ConnectError:
                last_error = RuntimeError("无法连接到 LLM API 服务器")
                if attempt < _MAX_RETRIES:
                    delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                    logger.warning(
                        "LLM connect error, attempt %d/%d, retrying in %ds",
                        attempt + 1,
                        _MAX_RETRIES,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise last_error
            except RuntimeError:
                raise
            except Exception as e:
                if attempt < _MAX_RETRIES:
                    delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                    logger.warning(
                        "LLM unexpected error: %s, attempt %d/%d",
                        str(e),
                        attempt + 1,
                        _MAX_RETRIES,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise

        raise last_error or RuntimeError("LLM API 调用失败")

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[str, None]:
        """Streaming chat completion. Yields content chunks.

        Note: Streaming does not retry mid-stream (would duplicate partial content).
        Retry only applies to the initial connection.
        """
        self._reload()
        if not self.api_key:
            # Simulate streaming for dev/demo
            logger.warning(
                "LLM MOCK MODE (stream): No API key configured — returning mock response. "
                "The generated content will be generic and NOT based on your worldview data. "
                "Configure API key via /settings page or .env file."
            )
            mock_text = _mock_response(messages)
            chunk_size = 20
            for i in range(0, len(mock_text), chunk_size):
                yield mock_text[i : i + chunk_size]
            return

        client = await self._get_client()
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
            "stream": True,
        }

        # Retry only on connection errors; once streaming starts, no retry
        for attempt in range(_MAX_RETRIES + 1):
            try:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                ) as resp:
                    if (
                        resp.status_code in _RETRY_STATUS_CODES
                        and attempt < _MAX_RETRIES
                    ):
                        delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                        logger.warning(
                            "LLM stream retryable error (HTTP %d), attempt %d/%d",
                            resp.status_code,
                            attempt + 1,
                            _MAX_RETRIES,
                        )
                        await asyncio.sleep(delay)
                        continue

                    if resp.status_code != 200:
                        body = await resp.aread()
                        try:
                            err_data = json.loads(body)
                            err_msg = err_data.get("error", {}).get(
                                "message", body.decode("utf-8", errors="replace")
                            )
                        except Exception:
                            err_msg = (
                                body.decode("utf-8", errors="replace")[:200]
                                or f"HTTP {resp.status_code}"
                            )
                        raise RuntimeError(
                            f"LLM API 错误 (HTTP {resp.status_code}): {err_msg}"
                        )

                    response_truncated = False
                    async for line in resp.aiter_lines():
                        if line.startswith("data: "):
                            data_str = line[6:]
                            if data_str.strip() == "[DONE]":
                                break
                            try:
                                data = json.loads(data_str)
                                choices = data.get("choices", [])
                                if choices:
                                    delta = choices[0].get("delta", {})
                                    if content := delta.get("content"):
                                        yield content
                                    # Detect truncation
                                    finish_reason = choices[0].get("finish_reason")
                                    if finish_reason == "length":
                                        response_truncated = True
                                        logger.warning(
                                            "LLM response truncated (finish_reason=length, max_tokens reached)"
                                        )
                            except (json.JSONDecodeError, KeyError, IndexError):
                                continue
                    if response_truncated:
                        raise LLMResponseTruncatedError(
                            "LLM 输出达到 token 上限，内容不完整，请提高最大输出 token 或降低目标字数"
                        )
                    return  # Success, exit retry loop

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                if attempt < _MAX_RETRIES:
                    delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                    logger.warning(
                        "LLM stream connection error: %s, attempt %d/%d",
                        str(e),
                        attempt + 1,
                        _MAX_RETRIES,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise RuntimeError(f"LLM API 连接失败: {e!s}")
            except RuntimeError:
                raise

    async def test_connection(self) -> dict:
        """Test the API connection with a minimal request."""
        self._reload()
        if not self.api_key:
            return {"success": False, "error": "API Key 未配置"}

        try:
            client = await self._get_client()
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "user", "content": '请回复"连接成功"四个字。'}
                    ],
                    "max_tokens": 20,
                    "temperature": 0,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                reply = data["choices"][0]["message"]["content"]
                return {"success": True, "reply": reply, "model": self.model}
            else:
                try:
                    error_data = resp.json()
                    error_msg = error_data.get("error", {}).get("message", resp.text)
                except Exception:
                    error_msg = resp.text[:200] if resp.text else "未知错误"
                return {
                    "success": False,
                    "error": f"HTTP {resp.status_code}: {error_msg}",
                }
        except httpx.ConnectError:
            return {"success": False, "error": "无法连接到 API 服务器，请检查 Base URL"}
        except httpx.TimeoutException:
            return {"success": False, "error": "请求超时，请检查网络连接"}
        except Exception as e:
            return {"success": False, "error": str(e)}


def _mock_response(messages: list[dict[str, str]]) -> str:
    """Generate a mock response when no API key is configured (dev mode)."""
    system_msg = next((m["content"] for m in messages if m["role"] == "system"), "")
    user_msg = next((m["content"] for m in messages if m["role"] == "user"), "")

    if "世界观" in system_msg and "提取" in system_msg:
        return _mock_worldview_extraction(user_msg)
    if "章节" in system_msg or "chapter" in system_msg.lower():
        return _mock_chapter(user_msg)
    return f"[开发模式] 未配置 LLM API Key，这是模拟响应。\n\n系统提示: {system_msg[:100]}...\n\n用户请求: {user_msg[:100]}..."


def _mock_worldview_extraction(user_msg: str) -> str:
    """Mock worldview extraction response for dev mode."""
    return """```json
{
  "characters": [
    {"name": "林远", "personality": "坚韧果敢，表面平和但内心有强烈的求知欲", "background": "出身偏远小镇，父母早亡，由祖父抚养长大", "motivation": "追寻父母失踪的真相，守护身边的人", "ability": "天生灵觉，能感知天地灵气波动", "relations": [{"name": "苏瑶", "relation": "青梅竹马/战友"}]},
    {"name": "苏瑶", "personality": "聪慧冷静，善于分析局势", "background": "苏家嫡女，家族势力庞大但内部暗流涌动", "motivation": "摆脱家族联姻命运，追求自由", "ability": "冰灵根，修炼天赋极高", "relations": [{"name": "林远", "relation": "青梅竹马/战友"}]},
    {"name": "秦长老", "personality": "深沉内敛，心怀天下", "background": "天玄宗大长老，修为深不可测", "motivation": "寻找化解天劫之法", "ability": "掌握上古阵法传承", "relations": [{"name": "林远", "relation": "引路人/师长"}]}
  ],
  "geography": [
    {"name": "苍澜大陆", "description": "故事的主大陆，分为东南西北四大区域，灵气浓度由中心向边缘递减", "significance": "主要故事发生地"},
    {"name": "青云镇", "description": "大陆东南边陲的小镇，是主角林远的故乡", "significance": "故事起点"},
    {"name": "天玄宗", "description": "苍澜大陆四大宗门之一，坐落于天玄山脉之巅", "significance": "主角修炼的主要场所"}
  ],
  "factions": [
    {"name": "天玄宗", "stance": "正道领袖", "power_level": "顶级", "relations": []},
    {"name": "暗影阁", "stance": "中立偏暗", "power_level": "一流", "relations": []},
    {"name": "魔道联盟", "stance": "邪恶", "power_level": "一流", "relations": []}
  ],
  "power_system": [
    {"name": "灵气修炼体系", "levels": "聚气境→筑基境→金丹境→元婴境→化神境→渡劫境→大乘境", "rules": "需吸收天地灵气修炼，境界越高突破越难", "limitations": "每个境界有瓶颈，强行突破会导致走火入魔"}
  ],
  "history": [
    {"event": "远古大战", "time": "万年前", "description": "上古大能与魔神之间的惊天大战，导致大陆灵气枯竭数千年", "impact": "许多远古传承失传，修炼体系断裂"},
    {"event": "灵气复苏", "time": "千年前", "description": "大陆灵气开始缓慢恢复，各宗门重新崛起", "impact": "新的修炼时代开启"}
  ],
  "conflicts": [
    {"name": "正邪之争", "type": "阵营冲突", "parties": "正道宗门 vs 魔道联盟", "stakes": "大陆的控制权与修炼资源的分配", "resolution_hint": "需找到第三条道路"},
    {"name": "天劫之危", "type": "生存危机", "parties": "全体修士 vs 天道规则", "stakes": "渡劫期以上修士面临天劫毁灭", "resolution_hint": "远古传承中隐藏着答案"}
  ],
  "special_settings": [
    {"name": "灵根天赋", "description": "每个人出生时拥有不同的灵根属性，决定修炼方向和速度", "rules": "灵根分为金木水火土五行及变异灵根，变异灵根极为稀有"},
    {"name": "天劫", "description": "达到渡劫境后，修士需经历天劫考验，失败则形神俱灭", "rules": "天劫强度与修士实力成正比，无法逃避"}
  ]
}
```"""


def _mock_chapter(user_msg: str) -> str:
    """Mock chapter content — uses worldview element names extracted from the prompt."""
    import re as _re

    # Extract element names from "本章需要揭示的世界观要素" section
    # Format: "  - name（category）: description"
    element_names = []
    for line in user_msg.split("\n"):
        stripped = line.strip()
        if stripped.startswith("- "):
            name_part = stripped[2:]
            # Cut at first ( or （ to get just the name
            for delim in ["(", "（", "：", ":"]:
                pos = name_part.find(delim)
                if pos > 0:
                    name_part = name_part[:pos]
                    break
            name = name_part.strip()
            if name and len(name) < 30 and name not in element_names:
                # Skip non-name lines like "本章为第一章"
                if not name.startswith("（") and not name.startswith("第"):
                    element_names.append(name)

    # Extract chapter title from "标题：xxx"
    title_match = _re.search(r"标题[：:]\s*(.+)", user_msg)
    title = title_match.group(1).strip() if title_match else ""

    # Build mock content using extracted names
    if element_names:
        char_name = element_names[0]
        other_names = element_names[1:] if len(element_names) > 1 else []
        other_str = "、".join(other_names) if other_names else "这个世界"

        content = f"""{title or "第章"}

{char_name}站在窗前，目光穿过城市的灯火，心中翻涌着复杂的情绪。

身后传来脚步声，是{other_str}派来的人。

"你确定要这么做吗？"来人的声音带着一丝犹豫。

{char_name}没有回头，只是淡淡地说："从一开始，就没有退路了。"

夜风从窗缝里灌进来，吹动了桌上的文件。那上面印着几个刺眼的大字——

一切的改变，就从今夜开始。

---

*这是开发模式的模拟章节内容。配置 LLM API Key 后将生成真实的 AI 内容。*"""
    else:
        content = f"""{title or "第一章"}

夜色如墨，万籁俱寂。

主角独自站在命运的十字路口，回想着近日发生的一切。那些看似偶然的相遇、那些不经意间的暗示，此刻串联成一条清晰的线索——

真相远比想象中更加复杂。

但无论如何，既然选择了这条路，就没有回头的道理。

前方的路或许充满荆棘，但也同样通向无限可能。

---

*这是开发模式的模拟章节内容。配置 LLM API Key 后将生成基于你世界观的 AI 内容。*"""

    return content


llm_client = LLMClient()
