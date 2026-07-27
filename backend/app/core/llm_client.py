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
                        resp.status_code, attempt + 1, _MAX_RETRIES, delay,
                    )
                    await asyncio.sleep(delay)
                    continue

                if resp.status_code != 200:
                    try:
                        err_data = resp.json()
                        err_msg = err_data.get("error", {}).get("message", resp.text)
                    except Exception:
                        err_msg = resp.text[:200] if resp.text else f"HTTP {resp.status_code}"
                    raise RuntimeError(f"LLM API 错误 (HTTP {resp.status_code}): {err_msg}")

                data = resp.json()
                return data["choices"][0]["message"]["content"]

            except httpx.TimeoutException:
                last_error = RuntimeError("LLM API 请求超时")
                if attempt < _MAX_RETRIES:
                    delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                    logger.warning("LLM timeout, attempt %d/%d, retrying in %ds", attempt + 1, _MAX_RETRIES, delay)
                    await asyncio.sleep(delay)
                    continue
                raise last_error
            except httpx.ConnectError:
                last_error = RuntimeError("无法连接到 LLM API 服务器")
                if attempt < _MAX_RETRIES:
                    delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                    logger.warning("LLM connect error, attempt %d/%d, retrying in %ds", attempt + 1, _MAX_RETRIES, delay)
                    await asyncio.sleep(delay)
                    continue
                raise last_error
            except RuntimeError:
                raise
            except Exception as e:
                if attempt < _MAX_RETRIES:
                    delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                    logger.warning("LLM unexpected error: %s, attempt %d/%d", str(e), attempt + 1, _MAX_RETRIES)
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
                    if resp.status_code in _RETRY_STATUS_CODES and attempt < _MAX_RETRIES:
                        delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                        logger.warning(
                            "LLM stream retryable error (HTTP %d), attempt %d/%d",
                            resp.status_code, attempt + 1, _MAX_RETRIES,
                        )
                        await asyncio.sleep(delay)
                        continue

                    if resp.status_code != 200:
                        body = await resp.aread()
                        try:
                            err_data = json.loads(body)
                            err_msg = err_data.get("error", {}).get("message", body.decode("utf-8", errors="replace"))
                        except Exception:
                            err_msg = body.decode("utf-8", errors="replace")[:200] or f"HTTP {resp.status_code}"
                        raise RuntimeError(f"LLM API 错误 (HTTP {resp.status_code}): {err_msg}")

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
                                        logger.warning("LLM response truncated (finish_reason=length, max_tokens reached)")
                            except (json.JSONDecodeError, KeyError, IndexError):
                                continue
                    return  # Success, exit retry loop

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                if attempt < _MAX_RETRIES:
                    delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                    logger.warning("LLM stream connection error: %s, attempt %d/%d", str(e), attempt + 1, _MAX_RETRIES)
                    await asyncio.sleep(delay)
                    continue
                raise RuntimeError(f"LLM API 连接失败: {str(e)}")
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
                        {"role": "user", "content": "请回复\"连接成功\"四个字。"}
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
                return {"success": False, "error": f"HTTP {resp.status_code}: {error_msg}"}
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
    if "大纲" in system_msg or "outline" in system_msg.lower():
        return _mock_outline(system_msg, user_msg)
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


def _mock_outline(system_msg: str, user_msg: str) -> str:
    """Mock outline response — generates chapters and reveal_plan matching the project's total_chapters."""
    import json as _json
    import re as _re

    # Parse total_chapters from the system prompt: "请为全部{N}章生成大纲"
    match = _re.search(r"全部(\d+)章", system_msg)
    total = int(match.group(1)) if match else 5

    # Extract element names from the user prompt's worldview data
    # Look for patterns like "  - name（priority）: description" in the prompt
    element_names = []
    for line in user_msg.split("\n"):
        stripped = line.strip()
        if stripped.startswith("- "):
            # Extract name: everything after "- " up to first ( or （ or :
            name_part = stripped[2:]
            for delim in ["(", "（", "：", ":"]:
                pos = name_part.find(delim)
                if pos > 0:
                    name_part = name_part[:pos]
                    break
            name = name_part.strip()
            if name and len(name) < 20 and name not in element_names:
                element_names.append(name)

    mock_titles = [
        "觉醒", "初入江湖", "暗流涌动", "风起云涌", "暗棋",
        "破茧", "风云际会", "暗夜追踪", "龙争虎斗", "破局",
        "逆流而上", "风暴前夕", "惊雷", "棋局", "暗战",
        "破阵", "逆袭", "巅峰对决", "真相", "抉择",
        "归来", "新的征程", "暗影之下", "破晓", "对决",
        "命运", "终章序曲", "最后的选择", "决战", "终章",
    ]

    phases = ["起势", "暗涌", "暗涌", "起势", "暗涌",
              "转折", "爆发", "爆发", "转折", "爆发",
              "深入", "深入", "爆发", "转折", "深入",
              "爆发", "深入", "爆发", "深入", "转折",
              "终局", "终局", "终局", "终局", "终局",
              "终局", "终局", "终局", "终局", "终局"]

    chapters = []
    reveal_plan = []
    for i in range(1, total + 1):
        title = mock_titles[i - 1] if i <= len(mock_titles) else f"第{i}章"
        phase = phases[i - 1] if i <= len(phases) else "推进"

        # Assign elements to chapters (spread across the story)
        reveal_elems = []
        if element_names:
            # Reveal 1-2 elements per chapter, cycling through
            idx = (i - 1) % max(len(element_names), 1)
            reveal_elems = [element_names[idx]]
            if i > 1 and len(element_names) > 1:
                idx2 = (i - 1 + 1) % len(element_names)
                if idx2 != idx:
                    reveal_elems.append(element_names[idx2])

        chapters.append({
            "chapter_num": i,
            "title": title,
            "summary": f"第{i}章内容概述，主角继续冒险，逐步揭示世界观设定。",
            "key_events": [f"关键事件{i}-1", f"关键事件{i}-2"],
            "reveal_elements": reveal_elems,
        })

        reveal_plan.append({
            "chapter": i,
            "phase": phase,
            "elements": reveal_elems,
            "summary": f"{phase}阶段，推进主线剧情",
        })

    # Build a comprehensive story_arc based on extracted worldview elements
    char_names = [n for n in element_names if n][:3]
    char_str = "、".join(char_names) if char_names else "主角"
    story_arc = (
        f"【核心主题】一个关于成长、抉择与命运的故事。{char_str}在世界观的设定下，"
        f"逐步发现自身与世界的深层联系，面对不断升级的矛盾和挑战，最终走向关键的终极抉择。\n"
        f"【主线脉络】故事以{char_str}的成长为线索，从平凡的起点出发，逐步卷入世界观中的核心矛盾。"
        f"随着力量的提升和视野的开阔，主角逐步揭开隐藏在世界表象之下的真相，"
        f"在各方势力的博弈中寻找自己的立场。\n"
        f"【关键矛盾】世界观中的核心矛盾推动主线发展，主角在势力冲突、理念对立和个人情感之间不断抉择。"
        f"随着故事推进，矛盾从个人层面逐步升级到世界观层面的终极冲突。\n"
        f"【角色弧线】{char_str}从普通少年成长为能够影响世界格局的关键人物，"
        f"在经历中不断修正自己的信念和目标。配角的加入丰富了故事维度，各自承担不同的叙事功能。\n"
        f"【世界观驱动】世界观中的力量体系决定成长节奏，地理设定影响剧情走向，"
        f"历史事件埋下伏笔并在后期回收，特殊设定为故事增添独特魅力。\n"
        f"【情感基调】整体氛围从平淡渐入高潮，前期侧重探索和成长，中段矛盾升级带来紧张感，"
        f"后期进入决战与抉择的高潮，最终以收束和余韵收尾。"
    )

    return "```json\n" + _json.dumps({
        "story_arc": story_arc,
        "reveal_plan": reveal_plan,
        "chapters": chapters,
    }, ensure_ascii=False, indent=2) + "\n```"


def _mock_chapter(user_msg: str) -> str:
    return """少年林远站在悬崖边，山风猎猎吹过他的衣袍。

脚下是无尽的深渊，身后是追杀者的脚步声。他攥紧了拳头，感受着体内那股陌生而强大的力量——三日前，他不过是个在小镇上砍柴度日的普通人。

"别跑了！"身后的声音带着嘲弄，"一个连聚气境都没到的废物，能逃到哪里去？"

林远没有回头。他在等——等体内那团灼热的光芒再次涌动。那是他坠入古洞时触碰到的神秘传承，一个改变了命运的意外。

风更大了。他闭上眼，感受着丹田中旋转的光团。

追杀者越来越近。

就在刀锋即将触及他后背的一瞬间，林远猛然睁开双眼，一道金光从他体内迸发而出——

那是整个大陆都已经消失了千年的力量。

---

*这是开发模式的模拟章节内容。配置 LLM API Key 后将生成真实的 AI 内容。*"""


llm_client = LLMClient()
