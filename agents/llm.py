"""Unified async LLM layer powered by OpenAI (ChatGPT / GPT-4o).

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


def get_provider() -> Optional[str]:
    key: str = os.getenv("OPENAI_API_KEY", "").strip()
    if key and key.lower() not in OPENAI_KEY_PLACEHOLDERS:
        return "openai"
    return None


def llm_available() -> bool:
    return get_provider() is not None


def llm_strict_mode() -> bool:
    default: str = "true" if llm_available() else "false"
    return os.getenv("LLM_STRICT_MODE", default).lower() in {"1", "true", "yes", "on"}


def get_model() -> str:
    if get_provider() == "openai":
        return os.getenv("OPENAI_MODEL", OPENAI_DEFAULT_MODEL)
    return "heuristic"


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
    if get_provider() == "openai":
        return await asyncio.wait_for(
            _openai_json(system, prompt, schema, schema_name),
            timeout=get_timeout_seconds(),
        )
    raise RuntimeError("No LLM provider configured (set OPENAI_API_KEY)")


async def complete_text(system: str, prompt: str) -> str:
    if get_provider() == "openai":
        return await asyncio.wait_for(
            _openai_text(system, prompt),
            timeout=get_timeout_seconds(),
        )
    raise RuntimeError("No LLM provider configured (set OPENAI_API_KEY)")


async def embed_texts(texts: list[str]) -> list[list[float]]:
    if get_provider() == "openai":
        return await asyncio.wait_for(_openai_embeddings(texts), timeout=get_timeout_seconds())
    raise RuntimeError("No LLM provider configured (set OPENAI_API_KEY)")


async def _openai_json(
    system: str, prompt: str, schema: Dict[str, Any], schema_name: str
) -> Dict[str, Any]:
    from openai import AsyncOpenAI

    client: AsyncOpenAI = AsyncOpenAI(timeout=get_timeout_seconds(), max_retries=0)
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

    client: AsyncOpenAI = AsyncOpenAI(timeout=get_timeout_seconds(), max_retries=0)
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
