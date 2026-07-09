import os

import httpx


def war_room_configured() -> bool:
    return bool(os.getenv("WAR_ROOM_WEBHOOK_URL", "").strip())


async def post_war_room(text: str) -> None:
    """Post a message to the incident war room (Slack or Discord incoming
    webhook, auto-detected from the URL). Never raises: notification failure
    must not break the investigation."""
    url: str = os.getenv("WAR_ROOM_WEBHOOK_URL", "").strip()
    if not url:
        return
    payload = {"content": text} if "discord" in url else {"text": text}
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(url, json=payload)
    except Exception as exc:
        print(f"[notify] war-room post failed: {exc}")
