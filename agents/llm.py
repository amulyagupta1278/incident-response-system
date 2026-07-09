"""Unified async LLM layer powered by OpenAI (ChatGPT / GPT-4o).

When no OPENAI_API_KEY is configured every caller falls back to its
heuristic path, so the system stays fully functional offline.
"""

import json
import os
from typing import Any, Dict, Optional

OPENAI_DEFAULT_MODEL: str = "gpt-4o"


def get_provider() -> Optional[str]:
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    return None


def llm_available() -> bool:
    return get_provider() is not None


def get_model() -> str:
    if get_provider() == "openai":
        return os.getenv("OPENAI_MODEL", OPENAI_DEFAULT_MODEL)
    return "heuristic"


async def complete_json(
    system: str, prompt: str, schema: Dict[str, Any], schema_name: str = "response"
) -> Dict[str, Any]:
    """Ask the configured LLM for a JSON response conforming to schema.

    Raises if no provider is configured or the call fails; callers are
    expected to catch and fall back to their heuristic path.
    """
    if get_provider() == "openai":
        return await _openai_json(system, prompt, schema, schema_name)
    raise RuntimeError("No LLM provider configured (set OPENAI_API_KEY)")


async def complete_text(system: str, prompt: str) -> str:
    if get_provider() == "openai":
        return await _openai_text(system, prompt)
    raise RuntimeError("No LLM provider configured (set OPENAI_API_KEY)")


async def _openai_json(
    system: str, prompt: str, schema: Dict[str, Any], schema_name: str
) -> Dict[str, Any]:
    from openai import AsyncOpenAI

    client: AsyncOpenAI = AsyncOpenAI()
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

    client: AsyncOpenAI = AsyncOpenAI()
    response: Any = await client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", OPENAI_DEFAULT_MODEL),
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
    )
    return response.choices[0].message.content or ""
