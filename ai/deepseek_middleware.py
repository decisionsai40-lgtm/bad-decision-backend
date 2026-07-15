"""
BAD DECISION — DeepSeek API Multi-Key Middleware
=================================================
This is the "brain" of the backend. It talks to the DeepSeek AI
to extract business data and find decision makers.

MULTI-KEY SYSTEM:
We support MULTIPLE API keys. When one key gets rate-limited (429),
we automatically switch to the next key — like rotating fresh pitchers
in a baseball game. If ALL keys are exhausted, we raise CriticalError.

HARD TOTAL DEADLINE:
The entire call (all keys, all retries) is capped at TOTAL_DEADLINE seconds.
This prevents a single DeepSeek call from blocking the worker for 9+ minutes
(which was the root cause of the "leads load forever then fail" bug).
"""

import httpx
import json
import asyncio
import time
from typing import Dict, Any, List

from config import DEEPSEEK_KEY_RING, DEEPSEEK_BASE_URL, DEEPSEEK_SCOUT_MODEL


class CriticalError(Exception):
    """All API keys are exhausted — the system cannot process requests."""
    pass


# Hard cap on the total time spent in execute_llm_payload, across all keys
# and all retries. Previously this was unbounded (3 keys × 3 attempts × 60s
# = up to 9 minutes), which caused task workers to hang indefinitely.
TOTAL_DEADLINE = 30  # seconds — hard ceiling for any single LLM call
PER_KEY_TIMEOUT = 12  # seconds — per-key request timeout (was 60)
RETRY_DELAY = 2  # seconds — pause between full sweeps of the key ring


async def execute_llm_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Send a single request to DeepSeek, cycling through API keys on rate limits.

    Bounded by TOTAL_DEADLINE: no matter how many keys are exhausted or how
    many timeouts occur, this function returns or raises within ~30 seconds.

    Args:
        payload: The request to send (includes model, messages, temperature, etc.)

    Returns:
        The AI's response as a dictionary

    Raises:
        CriticalError: If ALL keys are exhausted within the deadline
    """
    if not DEEPSEEK_KEY_RING:
        raise CriticalError(
            "No DeepSeek API keys configured! Set DEEPSEEK_API_KEY or DEEPSEEK_API_KEYS in .env"
        )

    deadline = time.monotonic() + TOTAL_DEADLINE
    sweep = 0

    while time.monotonic() < deadline:
        sweep += 1
        for key in DEEPSEEK_KEY_RING:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break

            # Per-key timeout = min(PER_KEY_TIMEOUT, remaining deadline)
            this_timeout = min(PER_KEY_TIMEOUT, remaining)

            try:
                async with httpx.AsyncClient(timeout=this_timeout) as client:
                    response = await client.post(
                        DEEPSEEK_BASE_URL,
                        headers={
                            "Authorization": f"Bearer {key}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                    )

                if response.status_code == 429:
                    print(f"[DEEPSEEK] Key ...{key[-4:]} rate limited (429) — trying next key")
                    continue

                if response.status_code == 200:
                    return response.json()

                print(f"[DEEPSEEK] Key ...{key[-4:]} error {response.status_code}: {response.text[:200]}")
                continue

            except httpx.TimeoutException:
                print(f"[DEEPSEEK] Key ...{key[-4:]} timed out ({this_timeout:.0f}s) — trying next key")
                continue
            except Exception as e:
                print(f"[DEEPSEEK] Key ...{key[-4:]} exception: {e}")
                continue

        # All keys failed on this sweep — pause and retry if we still have time
        remaining = deadline - time.monotonic()
        if remaining > RETRY_DELAY + 1:
            print(f"[DEEPSEEK] All keys exhausted (sweep {sweep}) — waiting {RETRY_DELAY}s before retry ({remaining:.0f}s remaining)")
            await asyncio.sleep(RETRY_DELAY)
        else:
            break

    raise CriticalError(
        f"GLOBAL_KEY_RING_EXHAUSTED — All DeepSeek API keys failed within {TOTAL_DEADLINE}s deadline "
        f"(sweeps={sweep}, keys={len(DEEPSEEK_KEY_RING)})"
    )


async def execute_llm_batch(
    items: List[Dict[str, Any]],
    build_prompt_fn,
    system_prompt: str = "You are a precise data extractor. Always respond with valid JSON.",
    batch_size: int = 10,
) -> List[Dict[str, Any]]:
    """
    Process multiple items in batches using a single DeepSeek call per batch.
    This is 10-20x faster and cheaper than calling execute_llm_payload once per item.

    Args:
        items: List of items to process (e.g., lead dicts)
        build_prompt_fn: Function that takes a list of items and returns a prompt string.
                         The prompt should ask DeepSeek to return a JSON array of results.
        system_prompt: System message for DeepSeek
        batch_size: How many items to process per API call (10 for free Render tier)

    Returns:
        List of result dicts (one per input item, in order)
    """
    results = []

    # Process in batches
    for i in range(0, len(items), batch_size):
        batch = items[i:i + batch_size]
        batch_num = i // batch_size + 1
        total_batches = (len(items) + batch_size - 1) // batch_size

        print(f"[DEEPSEEK-BATCH] Processing batch {batch_num}/{total_batches} ({len(batch)} items)")

        prompt = build_prompt_fn(batch)

        try:
            response = await execute_llm_payload({
                "model": DEEPSEEK_SCOUT_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.1,
            })

            content = response.get("choices", [{}])[0].get("message", {}).get("content", "{}")
            parsed = json.loads(content)

            # The response should have a "results" array
            batch_results = parsed.get("results", parsed.get("items", []))
            if isinstance(parsed, list):
                batch_results = parsed

            # Ensure we have exactly len(batch) results
            if len(batch_results) < len(batch):
                # Pad with empty results
                batch_results.extend([{} for _ in range(len(batch) - len(batch_results))])
            elif len(batch_results) > len(batch):
                batch_results = batch_results[:len(batch)]

            results.extend(batch_results)

        except Exception as e:
            print(f"[DEEPSEEK-BATCH] Error on batch {batch_num}: {e}")
            # On error, return empty results for this batch
            results.extend([{} for _ in range(len(batch))])

    return results
