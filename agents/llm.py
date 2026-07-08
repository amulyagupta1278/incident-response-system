"""Unified async LLM layer supporting OpenAI and Anthropic.

Provider selection:
  - LLM_PROVIDER=openai | anthropic | auto (default: auto)
  - auto prefers OPENAI_API_KEY, then ANTHROPIC_API_KEY.
When no key is configured every caller falls back to its heuristic path,
so the system stays fully functional offline.
"""

import json
import os
from typing import Any, Dict, Optional

OPENAI_DEFAULT_MODEL: str = "gpt-4o"
ANTHROPIC_DEFAULT_MODEL: str = "claude-opus-4-8"


def get_provider() -> Optional[str]:
    configured: str = os.getenv("LLM_PROVIDER", "auto").lower()
    has_openai: bool = bool(os.getenv("OPENAI_API_KEY"))
    has_anthropic: bool = bool(os.getenv("ANTHROPIC_API_KEY"))

    if configured == "openai":
        return "openai" if has_openai else None
    if configured == "anthropic":
        return "anthropic" if has_anthropic else None
    if has_openai:
        return "openai"
    if has_anthropic:
        return "anthropic"
    return None


def llm_available() -> bool:
    return get_provider() is not None


def get_model() -> str:
    provider: Optional[str] = get_provider()
    if provider == "openai":
        return os.getenv("OPENAI_MODEL", OPENAI_DEFAULT_MODEL)
    if provider == "anthropic":
        return os.getenv("ANTHROPIC_MODEL", ANTHROPIC_DEFAULT_MODEL)
    return "heuristic"


async def complete_json(
    system: str, prompt: str, schema: Dict[str, Any], schema_name: str = "response"
) -> Dict[str, Any]:
    """Ask the configured LLM for a JSON response conforming to schema.

    Raises if no provider is configured or the call fails; callers are
    expected to catch and fall back to their heuristic path.
    """
    provider: Optional[str] = get_provider()
    if provider == "openai":
        return await _openai_json(system, prompt, schema, schema_name)
    if provider == "anthropic":
        return await _anthropic_json(system, prompt, schema)
    raise RuntimeError("No LLM provider configured (set OPENAI_API_KEY or ANTHROPIC_API_KEY)")


async def complete_text(system: str, prompt: str) -> str:
    provider: Optional[str] = get_provider()
    if provider == "openai":
        return await _openai_text(system, prompt)
    if provider == "anthropic":
        return await _anthropic_text(system, prompt)
    raise RuntimeError("No LLM provider configured (set OPENAI_API_KEY or ANTHROPIC_API_KEY)")


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


async def _anthropic_json(system: str, prompt: str, schema: Dict[str, Any]) -> Dict[str, Any]:
    from anthropic import AsyncAnthropic

    client: AsyncAnthropic = AsyncAnthropic()
    message: Any = await client.messages.create(
        model=os.getenv("ANTHROPIC_MODEL", ANTHROPIC_DEFAULT_MODEL),
        max_tokens=8000,
        thinking={"type": "adaptive"},
        system=system,
        output_config={"format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": prompt}],
    )
    response_text: str = next(
        block.text for block in message.content if block.type == "text"
    )
    return json.loads(response_text)


async def _anthropic_text(system: str, prompt: str) -> str:
    from anthropic import AsyncAnthropic

    client: AsyncAnthropic = AsyncAnthropic()
    message: Any = await client.messages.create(
        model=os.getenv("ANTHROPIC_MODEL", ANTHROPIC_DEFAULT_MODEL),
        max_tokens=8000,
        thinking={"type": "adaptive"},
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return next(block.text for block in message.content if block.type == "text")
