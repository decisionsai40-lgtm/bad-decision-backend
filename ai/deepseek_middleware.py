"""
BAD DECISION AI — DeepSeek API Multi-Key Middleware
====================================================
Sends requests to DeepSeek AI with automatic key rotation.
"""

import httpx
from typing import Dict, Any
from config import DEEPSEEK_KEY_RING, DEEPSEEK_BASE_URL, DEEPSEEK_SCOUT_MODEL


class CriticalError(Exception):
    """All API keys are exhausted."""
    pass


async def execute_llm_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Send a request to DeepSeek AI, cycling through API keys on rate limits."""
    if not DEEPSEEK_KEY_RING:
        raise CriticalError("No DeepSeek API keys configured!")

    async with httpx.AsyncClient(timeout=90) as client:
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

                if response.status_code == 429:
                    print(f"[DEEPSEEK] Key ...{key[-4:]} rate limited — trying next")
                    continue

                if response.status_code == 200:
                    return response.json()

                print(f"[DEEPSEEK] Key ...{key[-4:]} error {response.status_code}: {response.text[:200]}")
                continue

            except httpx.TimeoutException:
                print(f"[DEEPSEEK] Key ...{key[-4:]} timed out — trying next")
                continue
            except Exception as e:
                print(f"[DEEPSEEK] Key ...{key[-4:]} exception: {e}")
                continue

    raise CriticalError("GLOBAL_KEY_RING_EXHAUSTED — All DeepSeek API keys are rate-limited or failing")
