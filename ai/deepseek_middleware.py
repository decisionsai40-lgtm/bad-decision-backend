"""
BAD DECISION AI — DeepSeek API Multi-Key Middleware
====================================================
This is the "brain" of the backend. It talks to the DeepSeek AI
to extract business data and find decision makers.

MULTI-KEY SYSTEM:
We support MULTIPLE API keys. Why? Because DeepSeek has rate limits
(only so many requests per minute on each key). When one key gets
tired (429 = "too many requests"), we automatically switch to the
next key in the ring — like rotating fresh pitchers in a baseball game.

If ALL keys are exhausted, we raise a CriticalError.
"""

import httpx
from typing import Dict, Any

from config import DEEPSEEK_KEY_RING, DEEPSEEK_BASE_URL, DEEPSEEK_SCOUT_MODEL


class CriticalError(Exception):
    """All API keys are exhausted — the system cannot process requests."""
    pass


async def execute_llm_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Send a request to the DeepSeek AI, cycling through API keys
    if we hit rate limits.

    Think of it like calling a pizza place:
    1. Call the first number → "We're too busy" (429)
    2. Call the second number → "We're too busy" (429)
    3. Call the third number → "Sure, here's your pizza!" (200)

    Args:
        payload: The request to send to DeepSeek
                (includes model, messages, temperature, etc.)

    Returns:
        The AI's response as a dictionary

    Raises:
        CriticalError: If ALL keys in the ring are exhausted
    """

    if not DEEPSEEK_KEY_RING:
        raise CriticalError("No DeepSeek API keys configured! Set DEEPSEEK_API_KEY or DEEPSEEK_API_KEYS in .env")

    async with httpx.AsyncClient(timeout=60) as client:
        for key in DEEPSEEK_KEY_RING:
            try:
                response = await client.post(
                    DEEPSEEK_BASE_URL,
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )

                # 429 = Rate limit hit → try next key
                if response.status_code == 429:
                    print(f"[DEEPSEEK] Key ...{key[-4:]} rate limited (429) — trying next key")
                    continue

                # 200 = Success!
                if response.status_code == 200:
                    return response.json()

                # Other errors (400, 401, 500, etc.)
                print(f"[DEEPSEEK] Key ...{key[-4:]} error {response.status_code}: {response.text[:200]}")
                continue

            except httpx.TimeoutException:
                print(f"[DEEPSEEK] Key ...{key[-4:]} timed out — trying next key")
                continue

            except Exception as e:
                print(f"[DEEPSEEK] Key ...{key[-4:]} exception: {e}")
                continue

    # ALL keys failed
    raise CriticalError("GLOBAL_KEY_RING_EXHAUSTED — All DeepSeek API keys are rate-limited or failing")
