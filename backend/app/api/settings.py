"""LLM settings API — manage API key, model, and connection testing."""

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.llm_client import llm_client
from app.core.settings_store import load_settings, save_settings

router = APIRouter(prefix="/api/settings", tags=["settings"])


class LLMSettingsRequest(BaseModel):
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o"
    temperature: float = 0.8
    max_tokens: int = 4096


class LLMSettingsResponse(BaseModel):
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o"
    temperature: float = 0.8
    max_tokens: int = 4096
    is_configured: bool = False


class TestConnectionResponse(BaseModel):
    success: bool
    reply: str | None = None
    model: str | None = None
    error: str | None = None


# Predefined provider presets
PROVIDER_PRESETS = [
    {
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"],
    },
    {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "models": ["deepseek-chat", "deepseek-coder", "deepseek-reasoner"],
    },
    {
        "name": "Moonshot (月之暗面)",
        "base_url": "https://api.moonshot.cn/v1",
        "models": ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
    },
    {
        "name": "Zhipu (智谱)",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": ["glm-4", "glm-4-flash", "glm-4-air"],
    },
    {
        "name": "通义千问 (DashScope)",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ["qwen-max", "qwen-plus", "qwen-turbo"],
    },
    {
        "name": "自定义 (OpenAI 兼容)",
        "base_url": "",
        "models": [],
    },
]


@router.get("/llm", response_model=LLMSettingsResponse)
async def get_llm_settings():
    """Get current LLM settings (API key is masked)."""
    s = load_settings()
    api_key = s.get("api_key", "")
    # Mask the key: show first 8 and last 4 chars
    masked_key = ""
    if api_key:
        if len(api_key) > 12:
            masked_key = api_key[:8] + "*" * (len(api_key) - 12) + api_key[-4:]
        else:
            masked_key = "*" * len(api_key)

    return LLMSettingsResponse(
        api_key=masked_key,
        base_url=s.get("base_url", "https://api.openai.com/v1"),
        model=s.get("model", "gpt-4o"),
        temperature=s.get("temperature", 0.8),
        max_tokens=s.get("max_tokens", 4096),
        is_configured=bool(api_key),
    )


@router.post("/llm", response_model=LLMSettingsResponse)
async def update_llm_settings(req: LLMSettingsRequest):
    """Save LLM settings. If api_key is all stars (masked), keep the existing key."""
    current = load_settings()

    # If the submitted key looks like a masked key (contains *), keep the existing one
    api_key = req.api_key
    if "*" in api_key and api_key != "":
        api_key = current.get("api_key", "")

    saved = save_settings({
        "api_key": api_key,
        "base_url": req.base_url,
        "model": req.model,
        "temperature": req.temperature,
        "max_tokens": req.max_tokens,
    })

    masked_key = ""
    if saved["api_key"]:
        k = saved["api_key"]
        if len(k) > 12:
            masked_key = k[:8] + "*" * (len(k) - 12) + k[-4:]
        else:
            masked_key = "*" * len(k)

    return LLMSettingsResponse(
        api_key=masked_key,
        base_url=saved["base_url"],
        model=saved["model"],
        temperature=saved["temperature"],
        max_tokens=saved["max_tokens"],
        is_configured=bool(saved["api_key"]),
    )


@router.post("/llm/test", response_model=TestConnectionResponse)
async def test_llm_connection():
    """Test the LLM API connection with current settings."""
    result = await llm_client.test_connection()
    return TestConnectionResponse(**result)


@router.get("/llm/providers")
async def get_providers():
    """Get predefined provider presets."""
    return {"providers": PROVIDER_PRESETS}
