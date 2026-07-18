"""Unified async LLM layer for Codex-style agents, backed by the OpenAI API.

When no OPENAI_API_KEY is configured callers may fall back to deterministic
heuristics. When a key exists, strict mode defaults on so LLM failures are
visible instead of silently producing fallback analysis.
"""

import asyncio
import json
import os
from typing import Any, Dict, Optional

OPENAI_DEFAULT_MODEL: str = "gpt-4o"
OPENAI_DEFAULT_EMBEDDING_MODEL: str = "text-embedding-3-small"
OPENAI_DEFAULT_TIMEOUT_SECONDS: float = 20.0
OPENAI_KEY_PLACEHOLDERS: set[str] = {
    "sk-your-key-here",
    "sk-your-openai-key",
    "your-openai-api-key",
    "<openai-api-key>",
    "<server-side key>",
}

PROVIDER_CONFIG: Dict[str, Dict[str, str]] = {
    "openai": {
        "key_env": "OPENAI_API_KEY", "model_env": "OPENAI_MODEL",
        "default_model": OPENAI_DEFAULT_MODEL, "base_url": "",
    },
    "gemini": {
        "key_env": "GEMINI_API_KEY", "model_env": "GEMINI_MODEL",
        "default_model": "gemini-2.5-flash",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
    },
    "groq": {
        "key_env": "GROQ_API_KEY", "model_env": "GROQ_MODEL",
        "default_model": "llama-3.3-70b-versatile",
        "base_url": "https://api.groq.com/openai/v1",
    },
    "openrouter": {
        "key_env": "OPENROUTER_API_KEY", "model_env": "OPENROUTER_MODEL",
        "default_model": "openai/gpt-4o-mini",
        "base_url": "https://openrouter.ai/api/v1",
    },
}


def _provider_details() -> tuple[Optional[str], Optional[Dict[str, str]]]:
    preferred = os.getenv("LLM_PROVIDER", "").strip().lower()
    order = ([preferred] if preferred in PROVIDER_CONFIG else []) + [
        name for name in PROVIDER_CONFIG if name != preferred
    ]
    for name in order:
        config = PROVIDER_CONFIG[name]
        key = os.getenv(config["key_env"], "").strip()
        if key and key.lower() not in OPENAI_KEY_PLACEHOLDERS:
            return name, config
    return None, None


def get_provider() -> Optional[str]:
    return _provider_details()[0]


def llm_available() -> bool:
    return get_provider() is not None


def llm_strict_mode() -> bool:
    default: str = "true" if llm_available() else "false"
    return os.getenv("LLM_STRICT_MODE", default).lower() in {"1", "true", "yes", "on"}


def get_model() -> str:
    _, config = _provider_details()
    return os.getenv(config["model_env"], config["default_model"]) if config else "heuristic"


def get_embedding_model() -> str:
    if get_provider() == "openai":
        return os.getenv("OPENAI_EMBEDDING_MODEL", OPENAI_DEFAULT_EMBEDDING_MODEL)
    return "heuristic"


def get_timeout_seconds() -> float:
    value: str = os.getenv("OPENAI_TIMEOUT_SECONDS", str(OPENAI_DEFAULT_TIMEOUT_SECONDS))
    try:
        return max(1.0, float(value))
    except ValueError:
        return OPENAI_DEFAULT_TIMEOUT_SECONDS


async def complete_json(
    system: str, prompt: str, schema: Dict[str, Any], schema_name: str = "response"
) -> Dict[str, Any]:
    """Ask the configured LLM for a JSON response conforming to schema.

    Raises if no provider is configured or the call fails; callers are
    expected to catch and fall back to their heuristic path.
    """
    if get_provider():
        return await asyncio.wait_for(
            _openai_json(system, prompt, schema, schema_name),
            timeout=get_timeout_seconds(),
        )
    raise RuntimeError("No LLM provider configured")


async def complete_text(system: str, prompt: str) -> str:
    if get_provider():
        return await asyncio.wait_for(
            _openai_text(system, prompt),
            timeout=get_timeout_seconds(),
        )
    raise RuntimeError("No LLM provider configured")


async def embed_texts(texts: list[str]) -> list[list[float]]:
    if get_provider() == "openai":
        return await asyncio.wait_for(_openai_embeddings(texts), timeout=get_timeout_seconds())
    raise RuntimeError("No LLM provider configured (set OPENAI_API_KEY)")


async def _openai_json(
    system: str, prompt: str, schema: Dict[str, Any], schema_name: str
) -> Dict[str, Any]:
    from openai import AsyncOpenAI

    client = _openai_compatible_client(AsyncOpenAI)
    response: Any = await client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", OPENAI_DEFAULT_MODEL),
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": schema_name, "strict": True, "schema": schema},
        },
    )
    return json.loads(response.choices[0].message.content)


async def _openai_text(system: str, prompt: str) -> str:
    from openai import AsyncOpenAI

    client = _openai_compatible_client(AsyncOpenAI)
    response: Any = await client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", OPENAI_DEFAULT_MODEL),
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
    )
    return response.choices[0].message.content or ""


async def _openai_embeddings(texts: list[str]) -> list[list[float]]:
    from openai import AsyncOpenAI

    client: AsyncOpenAI = AsyncOpenAI(timeout=get_timeout_seconds(), max_retries=0)
    response: Any = await client.embeddings.create(
        model=get_embedding_model(),
        input=texts,
    )
    return [item.embedding for item in response.data]


def _openai_compatible_client(client_class: Any) -> Any:
    provider, config = _provider_details()
    if not provider or not config:
        raise RuntimeError("No LLM provider configured")
    kwargs: Dict[str, Any] = {
        "api_key": os.getenv(config["key_env"], ""),
        "timeout": get_timeout_seconds(),
        "max_retries": 0,
    }
    base_url = os.getenv(f"{provider.upper()}_BASE_URL", "").strip() or config["base_url"]
    if base_url:
        kwargs["base_url"] = base_url
    return client_class(**kwargs)
