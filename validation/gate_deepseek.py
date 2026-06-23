"""
BAD DECISION — Gate 3: DeepSeek AI Verification
================================================
This is the most advanced check. We use DeepSeek's reasoning model
(deepseek-reasoner) to analyze an email address and determine:

  1. Is this a REAL person's email, or a role address? (info@, sales@, admin@)
  2. Is this a catch-all domain? (the SMTP gate already checks this, but
     DeepSeek can catch patterns the SMTP probe misses)
  3. Is the email format likely to reach a human?

This gate runs ONLY for Pro tier users (the "Guaranteed Deliverable" tier).

Speed: Medium (3-8 seconds per email, can be batched)
Who gets it: Pro tier ONLY
"""

import json
from typing import Tuple

from ai.deepseek_middleware import execute_llm_payload
from config import DEEPSEEK_SCHOLAR_MODEL


# Common role-based email prefixes that are NOT individual people
ROLE_PREFIXES = {
    "info", "sales", "admin", "support", "contact", "help", "office",
    "mail", "noreply", "no-reply", "donotreply", "do-not-reply",
    "marketing", "hr", "finance", "billing", "accounts", "reception",
    "enquiries", "inquiries", "general", "hello", "team", "staff",
    "service", "customer", "customerservice", "abuse", "postmaster",
    "webmaster", "security", "legal", "jobs", "careers",
}


def is_role_address(email_address: str) -> bool:
    """
    Quick local check: is this email a role address (info@, sales@, etc.)?
    Role addresses go to a group inbox, not a specific person.
    """
    if not email_address or "@" not in email_address:
        return False

    prefix = email_address.split("@")[0].lower().strip()
    return prefix in ROLE_PREFIXES


async def check_deepseek(email_address: str, company_name: str = "") -> Tuple[bool, bool, str]:
    """
    Use DeepSeek to verify an email is a real person (not a role address).

    Args:
        email_address: The email to verify
        company_name: The company name (helps DeepSeek contextualize)

    Returns:
        (is_valid, is_role, reason)
        - is_valid: True = likely a real person's email
        - is_role: True = this is a role/group address (info@, sales@)
        - reason: Human-readable explanation
    """
    if not email_address or email_address == "ABSENT":
        return False, False, "No email provided"

    # Quick local check first (saves a DeepSeek call for obvious role addresses)
    if is_role_address(email_address):
        print(f"[GATE3-DEEPSEEK] {email_address} — ROLE ADDRESS (local check)")
        return True, True, "Role address (e.g. info@, sales@) — not a specific person"

    try:
        prompt = f"""
        You are an email verification expert. Analyze this email address and determine
        if it belongs to a REAL PERSON or if it is a ROLE/GROUP address.

        Email: {email_address}
        Company: {company_name}

        A ROLE address goes to a group inbox (info@, sales@, admin@, support@, etc.).
        A PERSONAL address goes to a specific human (john@, jane.doe@, jsmith@).

        Also check: does the email format look legitimate? Is the domain suspicious?

        Respond with a JSON object:
        {{
            "is_personal": true/false,
            "is_role": true/false,
            "confidence": 0.0-1.0,
            "reason": "brief explanation"
        }}
        """

        response = await execute_llm_payload({
            "model": DEEPSEEK_SCHOLAR_MODEL,
            "messages": [
                {"role": "system", "content": "You are an email verification AI. Analyze email addresses and determine if they are personal or role-based. Always respond with valid JSON."},
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
            "max_tokens": 200,
        })

        content = response.get("choices", [{}])[0].get("message", {}).get("content", "{}")

        # DeepSeek sometimes returns malformed JSON (unterminated string, trailing comma, etc.)
        # Try strict parse first, then fall back to lenient extraction.
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as parse_err:
            print(f"[GATE3-DEEPSEEK] {email_address} — JSON parse failed ({parse_err}), attempting regex extraction")
            import re
            def _extract_bool(key: str) -> bool:
                m = re.search(rf'"{key}"\s*:\s*(true|false)', content, re.IGNORECASE)
                return m.group(1).lower() == "true" if m else False
            def _extract_float(key: str, default: float = 0.5) -> float:
                m = re.search(rf'"{key}"\s*:\s*([0-9]*\.?[0-9]+)', content)
                try:
                    return float(m.group(1)) if m else default
                except (ValueError, IndexError):
                    return default
            parsed = {
                "is_personal": _extract_bool("is_personal"),
                "is_role": _extract_bool("is_role"),
                "confidence": _extract_float("confidence", 0.5),
                "reason": "Extracted from partial JSON response",
            }
            print(f"[GATE3-DEEPSEEK] {email_address} — Recovered fields: {parsed}")

        is_personal = parsed.get("is_personal", True)
        is_role = parsed.get("is_role", False)
        reason = parsed.get("reason", "No reason provided")
        confidence = parsed.get("confidence", 0.5)

        # If DeepSeek is not confident, be lenient (accept the email)
        if confidence < 0.6:
            print(f"[GATE3-DEEPSEEK] {email_address} — Low confidence ({confidence}), accepting")
            return True, is_role, f"Low confidence verification: {reason}"

        if is_personal and not is_role:
            print(f"[GATE3-DEEPSEEK] {email_address} — VERIFIED as personal (confidence: {confidence})")
            return True, False, reason

        if is_role:
            print(f"[GATE3-DEEPSEEK] {email_address} — ROLE ADDRESS (confidence: {confidence})")
            return True, True, reason

        print(f"[GATE3-DEEPSEEK] {email_address} — REJECTED (confidence: {confidence}): {reason}")
        return False, False, reason

    except Exception as e:
        print(f"[GATE3-DEEPSEEK] {email_address} — Error: {e}")
        # On AI errors, be lenient (don't drop leads due to AI failures)
        return True, is_role_address(email_address), f"AI verification unavailable: {str(e)}"
