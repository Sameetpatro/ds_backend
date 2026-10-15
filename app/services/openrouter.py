# app/services/openrouter.py
# Unified LLM caller supporting DeepSeek (native) and OpenRouter.
# Import path for existing code: `from app.services import call_openrouter`
# (also aliased as `call_llm`).

import os
import httpx
from dotenv import load_dotenv

load_dotenv()

_LLM_TIMEOUT = 60.0


def get_llm_credentials():
    """
    Returns (api_key, endpoint_url, model_name).
    Prioritizes DEEPSEEK_API_KEY if present, then OPENROUTER_API_KEY,
    allowing explicit overrides via LLM_API_KEY, LLM_BASE_URL, and LLM_MODEL.
    """
    api_key = os.getenv("LLM_API_KEY")
    base_url = os.getenv("LLM_BASE_URL")
    model = os.getenv("LLM_MODEL")

    if not api_key:
        if os.getenv("GROQ_API_KEY"):
            api_key = os.getenv("GROQ_API_KEY")
            base_url = base_url or "https://api.groq.com/openai/v1/chat/completions"
            model = model or "openai/gpt-oss-120b"
        elif os.getenv("GEMINI_API_KEY"):
            api_key = os.getenv("GEMINI_API_KEY")
            base_url = base_url or "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
            model = model or "gemini-1.5-flash"
        elif os.getenv("DEEPSEEK_API_KEY"):
            api_key = os.getenv("DEEPSEEK_API_KEY")
            base_url = base_url or "https://api.deepseek.com/v1/chat/completions"
            model = model or "deepseek-chat"
        elif os.getenv("OPENROUTER_API_KEY"):
            api_key = os.getenv("OPENROUTER_API_KEY")
            base_url = base_url or "https://openrouter.ai/api/v1/chat/completions"
            model = model or "openai/gpt-4o-mini"
        else:
            raise RuntimeError(
                "No LLM API key found. Set GROQ_API_KEY, GEMINI_API_KEY, DEEPSEEK_API_KEY, or OPENROUTER_API_KEY."
            )

    # Normalize endpoint URL
    if not base_url:
        base_url = "https://api.deepseek.com/v1/chat/completions"
    elif not base_url.endswith("/chat/completions"):
        base_url = base_url.rstrip("/") + "/chat/completions"

    if not model:
        model = "deepseek-chat" if "deepseek" in base_url else "openai/gpt-4o-mini"

    return api_key, base_url, model


async def call_openrouter(messages: list) -> str:
    """
    Sends messages to the configured LLM provider (DeepSeek or OpenRouter)
    and returns the assistant reply text.
    """
    api_key, url, model = get_llm_credentials()

    async with httpx.AsyncClient(timeout=_LLM_TIMEOUT) as client:
        response = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type":  "application/json",
            },
            json={
                "model":    model,
                "messages": messages,
            }
        )

    if response.status_code != 200:
        provider = "DeepSeek" if "deepseek" in url else "LLM"
        raise RuntimeError(f"{provider} error {response.status_code}: {response.text}")

    data = response.json()
    return data["choices"][0]["message"]["content"]


# Alias for modern callers
call_llm = call_openrouter