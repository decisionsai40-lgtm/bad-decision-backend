"""
BAD DECISION — Messaging Platform Checker
==========================================
Checks if phone numbers are registered on WhatsApp and Telegram.
Uses CheckNumber.ai API (1,000 free checks, then $0.30 per 10K).

Usage:
    result = await check_messaging_platforms("+1234567890")
    # result = {"whatsapp": True, "telegram": False}
"""
import httpx
import os
from typing import Dict, Optional

CHECKNUMBER_API_KEY = os.getenv("CHECKNUMBER_API_KEY", "").strip()
CHECKNUMBER_API_BASE = "https://api.checknumber.ai/v1"


async def check_messaging_platforms(phone_number: str) -> Dict[str, bool]:
    """
    Check if a phone number is registered on WhatsApp and Telegram.

    Args:
        phone_number: Phone number in E.164 format (e.g., "+1234567890")

    Returns:
        {"whatsapp": bool, "telegram": bool}
        Returns all False if API key is not set or check fails.
    """
    if not CHECKNUMBER_API_KEY:
        return {"whatsapp": False, "telegram": False}

    if not phone_number or phone_number == "ABSENT":
        return {"whatsapp": False, "telegram": False}

    # Normalize phone number (strip spaces, dashes, parentheses)
    cleaned = "".join(c for c in phone_number if c.isdigit() or c == "+")
    if not cleaned.startswith("+"):
        cleaned = "+" + cleaned

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                f"{CHECKNUMBER_API_BASE}/check",
                headers={
                    "Authorization": f"Bearer {CHECKNUMBER_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "number": cleaned,
                    "platforms": ["whatsapp", "telegram"],
                },
            )

            if response.status_code == 429:
                print("[CHECKNUMBER] Rate limited (429)")
                return {"whatsapp": False, "telegram": False}

            if response.status_code != 200:
                print(f"[CHECKNUMBER] Error {response.status_code}: {response.text[:200]}")
                return {"whatsapp": False, "telegram": False}

            data = response.json()
            return {
                "whatsapp": bool(data.get("whatsapp", False)),
                "telegram": bool(data.get("telegram", False)),
            }

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
