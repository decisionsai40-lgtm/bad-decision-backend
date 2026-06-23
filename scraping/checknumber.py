"""
BAD DECISION — Messaging Platform Checker
==========================================
Checks if phone numbers are registered on WhatsApp and Telegram.
Uses CheckNumber.ai API.

API details (verified 23 June 2026):
  - Endpoint: https://api.checknumber.ai/v1/check
  - Auth header: X-APP-KEY (NOT Bearer)
  - Request body: {"phone": "+1234567890", "service": "whatsapp"}  (one service per call)
  - Response: {"status": "success", "is_registered": true/false, ...}

Usage:
    result = await check_messaging_platforms("+1234567890")
    # result = {"whatsapp": True, "telegram": False}
"""
import httpx
import os
import asyncio
from typing import Dict

CHECKNUMBER_API_KEY = os.getenv("CHECKNUMBER_API_KEY", "").strip()
CHECKNUMBER_API_BASE = "https://api.checknumber.ai/v1"


async def _check_single_platform(client: httpx.AsyncClient, phone: str, service: str) -> bool:
    """Check a single platform (whatsapp or telegram) for a phone number."""
    try:
        response = await client.post(
            f"{CHECKNUMBER_API_BASE}/check",
            headers={
                "X-APP-KEY": CHECKNUMBER_API_KEY,
                "Content-Type": "application/json",
            },
            json={
                "phone": phone,
                "service": service,
            },
        )

        if response.status_code == 429:
            print(f"[CHECKNUMBER] Rate limited (429) for {phone} / {service}")
            return False

        if response.status_code != 200:
            print(f"[CHECKNUMBER] Error {response.status_code} for {phone}/{service}: {response.text[:200]}")
            return False

        data = response.json()

        # The API may return the result under various field names.
        # Check all known variants:
        #   is_registered, registered, exists, found, active, on_whatsapp, on_telegram
        # Also check nested: {result: {is_registered: true}}
        result_obj = data.get("result", data) if isinstance(data, dict) else {}

        for key in (f"on_{service}", "is_registered", "registered", "exists", "found", "active"):
            val = result_obj.get(key) if isinstance(result_obj, dict) else data.get(key) if isinstance(data, dict) else None
            if val is not None:
                return bool(val)

        # If status is "success" and no explicit boolean, check if status indicates registered
        status = (data.get("status") or "").lower() if isinstance(data, dict) else ""
        if status == "registered" or status == "found":
            return True

        print(f"[CHECKNUMBER] {phone}/{service} — couldn't parse response: {str(data)[:200]}")
        return False

    except Exception as e:
        print(f"[CHECKNUMBER] Error checking {phone}/{service}: {e}")
        return False


async def check_messaging_platforms(phone_number: str) -> Dict[str, bool]:
    """
    Check if a phone number is registered on WhatsApp and Telegram.

    Args:
        phone_number: Phone number (any format — we normalize to E.164)

    Returns:
        {"whatsapp": bool, "telegram": bool}
    """
    if not CHECKNUMBER_API_KEY:
        if not hasattr(check_messaging_platforms, '_warned_no_key'):
            print("[CHECKNUMBER] WARNING: CHECKNUMBER_API_KEY is not set. WhatsApp/Telegram detection disabled.")
            check_messaging_platforms._warned_no_key = True
        return {"whatsapp": False, "telegram": False}

    if not phone_number or phone_number == "ABSENT":
        return {"whatsapp": False, "telegram": False}

    # Normalize phone number (strip spaces, dashes, parentheses)
    cleaned = "".join(c for c in phone_number if c.isdigit() or c == "+")
    if not cleaned.startswith("+"):
        cleaned = "+" + cleaned

    # Skip obviously fake numbers
    digits_only = cleaned.lstrip("+")
    if len(digits_only) < 7 or digits_only in ("9999999999", "0000000000", "1234567890"):
        return {"whatsapp": False, "telegram": False}

    print(f"[CHECKNUMBER] Checking {cleaned} for WhatsApp + Telegram...")

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # Check both platforms concurrently
            whatsapp_task = _check_single_platform(client, cleaned, "whatsapp")
            telegram_task = _check_single_platform(client, cleaned, "telegram")
            whatsapp_result, telegram_result = await asyncio.gather(
                whatsapp_task, telegram_task, return_exceptions=True
            )

            # Handle exceptions from gather
            if isinstance(whatsapp_result, Exception):
                print(f"[CHECKNUMBER] WhatsApp check exception for {cleaned}: {whatsapp_result}")
                whatsapp_result = False
            if isinstance(telegram_result, Exception):
                print(f"[CHECKNUMBER] Telegram check exception for {cleaned}: {telegram_result}")
                telegram_result = False

            result = {
                "whatsapp": bool(whatsapp_result),
                "telegram": bool(telegram_result),
            }
            print(f"[CHECKNUMBER] {cleaned} → WhatsApp={result['whatsapp']}, Telegram={result['telegram']}")
            return result

    except Exception as e:
        print(f"[CHECKNUMBER] Error checking {phone_number}: {e}")
        return {"whatsapp": False, "telegram": False}


async def check_messaging_batch(phone_numbers: list) -> list:
    """
    Check multiple phone numbers at once.
    Returns list of {"phone": str, "whatsapp": bool, "telegram": bool}.
    """
    if not CHECKNUMBER_API_KEY:
        return [{"phone": p, "whatsapp": False, "telegram": False} for p in phone_numbers]

    results = []
    for phone in phone_numbers:
        result = await check_messaging_platforms(phone)
        results.append({"phone": phone, **result})

    return results
