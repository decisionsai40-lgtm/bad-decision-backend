"""
BAD DECISION — Outreach Message Generator
==========================================

Generates 3 personalized outreach messages per lead:
  1. EMAIL message  (500-530 chars, strict)
  2. SOCIAL DM      (500-530 chars, strict)
  3. CALL SCRIPT    (500-530 chars, strict)

STRICT CHARACTER ENFORCEMENT:
  - Every message is validated to be between 500 and 530 characters (inclusive).
  - If DeepSeek returns a message outside that range, we do ONE regeneration pass
    with an explicit "your last attempt was N chars, must be 500-530" instruction.
  - If still out of range after the retry, we post-process:
      * Too long  → trim at the last sentence boundary ≤ 530 chars, else hard-cut at 527 + "...".
      * Too short → append a short call-to-action sentence to reach ≥ 500 chars.
  - This guarantees the UI always shows messages in the 500-530 window.

COPYWRITING STYLES (6):
  dan_kennedy, donald_miller, ray_edwards, david_ogilvy, jay_abraham, gary_halbert

NO PLACEHOLDERS:
  Messages must be ready-to-send — no [Your Name], [Company], etc.
  The user's service + audience are injected into the prompt so DeepSeek
  writes a complete, specific message.
"""

import json
import re
from typing import Dict, Any

from ai.deepseek_middleware import execute_llm_payload, CriticalError
from config import DEEPSEEK_SCOUT_MODEL


# ============================================================
# CHARACTER LIMITS — strict enforcement
# ============================================================
MIN_CHARS = 500
MAX_CHARS = 530

# ============================================================
# COPYWRITING STYLE PROMPTS
# ============================================================
STYLE_PROMPTS: Dict[str, str] = {
    "dan_kennedy": (
        "Write in Dan Kennedy's no-nonsense direct-response style: "
        "punchy opener, a specific pain point, a concrete promise with a deadline, "
        "and a single clear call-to-action. Conversational but urgent."
    ),
    "donald_miller": (
        "Write in Donald Miller's StoryBrand style: "
        "position the recipient as the hero with a problem, present the sender as the guide, "
        "give a clear plan, and call them to action. Warm and clarifying."
    ),
    "ray_edwards": (
        "Write in Ray Edwards' copy style: "
        "head-style hook, empathy for the struggle, a bold promise, proof-ish detail, "
        "and an enthusiastic call-to-action. Confident and hopeful."
    ),
    "david_ogilvy": (
        "Write in David Ogilvy's advertising style: "
        "researched, factual, respectful of the reader's intelligence. "
        "Lead with a specific benefit, support with a concrete detail, close with a soft ask."
    ),
    "jay_abraham": (
        "Write in Jay Abraham's style: "
        "focus on the recipient's hidden underutilized asset/opportunity, "
        "reframe the risk, propose a low-friction next step. Strategic and generous."
    ),
    "gary_halbert": (
        "Write in Gary Halbert's style: "
        "provocative opener that stops the scroll, a vivid scenario, a big claim, "
        "and a direct ask. Bold, colloquial, slightly irreverent."
    ),
}


# ============================================================
# PROMPT BUILDER
# ============================================================
def _build_prompt(lead: Dict[str, Any], user_service: str, target_audience: str, style: str) -> str:
    """Build the user-message prompt for DeepSeek."""
    company = lead.get("company_name") or lead.get("author_username") or "your business"
    dm_name = lead.get("dm_name") or ""
    dm_position = lead.get("dm_position") or ""
    website = lead.get("website_url") or ""
    email = lead.get("verified_email") or ""
    phone = lead.get("phone") or ""
    intent = lead.get("intent_text") or lead.get("intent_level") or ""

    style_instruction = STYLE_PROMPTS.get(style, STYLE_PROMPTS["david_ogilvy"])

    dm_line = f"- Decision Maker: {dm_name} ({dm_position})" if dm_name else "- Decision Maker: (unknown)"

    lead_context = f"""LEAD CONTEXT:
- Company / Name: {company}
{dm_line}
- Website: {website}
- Email: {email}
- Phone: {phone}
- Intent signal: {intent}"""

    sender_context = f"""SENDER CONTEXT:
- What the sender sells: {user_service}
- Who the sender's ideal customer is: {target_audience or 'businesses like this one'}"""

    char_rule = f"""CHARACTER LIMIT (CRITICAL):
Every message MUST be between {MIN_CHARS} and {MAX_CHARS} characters (inclusive).
Count every character including spaces and punctuation.
- If a message is under {MIN_CHARS} chars, expand it with more specific detail.
- If a message is over {MAX_CHARS} chars, trim it — but keep it complete and readable.
Do NOT include subject lines, greetings-only lines, or signatures as separate fields.
The message body itself must hit the character target."""

    return f"""You are an expert copywriter writing personalized outreach messages.

{style_instruction}

{lead_context}

{sender_context}

{char_rule}

TASK:
Write THREE distinct outreach messages for this lead. Each must be a complete,
ready-to-send message with NO placeholders (no [Your Name], no [Company], etc.).

1. "email_message" — a cold email. Include a greeting using the DM name if known,
   a body that connects the sender's service to the lead's situation, and a soft
   call-to-action. Sign off as "Alex from Bad Decision" (or omit sign-off if it
   pushes you over the limit).

2. "social_message" — a LinkedIn or Instagram DM. More casual, shorter sentences,
   no subject line. Can reference the lead's website or business specifically.

3. "call_script" — a phone call opening script (what to say in the first 30 seconds).
   Include a greeting, a reason for calling tied to the lead's business, and a
   permission-based question to continue the conversation.

Return ONLY a JSON object with exactly these keys:
{{
  "email_message": "...",
  "social_message": "...",
  "call_script": "..."
}}

Remember: each of the three strings MUST be between {MIN_CHARS} and {MAX_CHARS} characters."""


# ============================================================
# CHARACTER ENFORCEMENT — post-processing
# ============================================================
def _enforce_length(message: str, kind: str = "message") -> str:
    """
    Force a message into the [MIN_CHARS, MAX_CHARS] window.
    Called after DeepSeek returns, and again after a retry if needed.
    """
    if not message:
        message = ""

    # Strip leading/trailing whitespace but preserve internal structure
    message = message.strip()

    # If already in range, return as-is
    if MIN_CHARS <= len(message) <= MAX_CHARS:
        return message

    # TOO LONG → trim at sentence boundary
    if len(message) > MAX_CHARS:
        # Try to cut at the last sentence-ending punctuation (., !, ?) before MAX_CHARS
        cut_zone = message[:MAX_CHARS]
        # Find the last sentence boundary in the last 80 chars of the zone
        boundary = -1
        for punct in ['. ', '! ', '? ', '."', '!"', '?"']:
            idx = cut_zone.rfind(punct)
            if idx > boundary:
                boundary = idx
        if boundary >= MIN_CHARS - 20:  # only use boundary if it keeps us ≥ ~480
            trimmed = cut_zone[:boundary + 1].rstrip()
        else:
            # Hard cut at MAX_CHARS - 3 and add ellipsis
            trimmed = cut_zone[:MAX_CHARS - 3].rstrip() + "..."
        # Final safety: if still over (shouldn't be), hard cut
        if len(trimmed) > MAX_CHARS:
            trimmed = trimmed[:MAX_CHARS - 3].rstrip() + "..."
        return trimmed

    # TOO SHORT → pad with a call-to-action sentence
    if len(message) < MIN_CHARS:
        shortfall = MIN_CHARS - len(message)
        # Build a CTA that makes sense for any outreach message
        cta_options = [
            " Reply to this message and I'll send over a short breakdown.",
            " Hit reply with 'yes' and I'll forward the details today.",
            " Want me to send the full breakdown? Just reply here.",
            " Let me know a good time and I'll walk you through it.",
            " Reply 'send it' and I'll forward everything you need.",
        ]
        for cta in cta_options:
            if len(message) + len(cta) <= MAX_CHARS and len(message) + len(cta) >= MIN_CHARS:
                return message + cta
        # If a single CTA doesn't get us into range, append until we hit the window
        for cta in cta_options:
            if len(message) >= MIN_CHARS:
                break
            if len(message) + len(cta) <= MAX_CHARS:
                message += cta
        # Last resort: if still under, pad with a generic closer
        if len(message) < MIN_CHARS:
            pad_needed = MIN_CHARS - len(message)
            message += " " + ("Looking forward to your reply." * 10)[:pad_needed]
        # Final safety
        if len(message) > MAX_CHARS:
            message = message[:MAX_CHARS - 3].rstrip() + "..."
        return message

    return message


# ============================================================
# MAIN GENERATION FUNCTION
# ============================================================
async def generate_outreach_messages(
    lead: Dict[str, Any],
    user_service: str,
    target_audience: str,
    copywriting_style: str,
) -> Dict[str, str]:
    """
    Generate 3 personalized outreach messages for a single lead.

    Args:
        lead: The lead dict from workspace_leads
        user_service: What the user sells (from their profile)
        target_audience: Who the user's ideal customer is
        copywriting_style: One of dan_kennedy, donald_miller, ray_edwards,
                           david_ogilvy, jay_abraham, gary_halbert

    Returns:
        Dict with keys: email_message, social_message, call_script
        Each value is a string between 500 and 530 characters (inclusive).

    Raises:
        CriticalError: If all DeepSeek keys are exhausted
        Exception: On other failures
    """
    if not user_service:
        raise ValueError("user_service is required — tell the user to set up their service in Settings first.")

    style = copywriting_style if copywriting_style in STYLE_PROMPTS else "david_ogilvy"
    prompt = _build_prompt(lead, user_service, target_audience, style)

    # ---------- PASS 1: initial generation ----------
    response = await execute_llm_payload({
        "model": DEEPSEEK_SCOUT_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are an expert direct-response copywriter. You ALWAYS respond with "
                    "valid JSON. You are obsessive about character counts — if asked for "
                    "500-530 characters, you deliver exactly that range."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.7,
    })

    content = response.get("choices", [{}])[0].get("message", {}).get("content", "{}")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        # Try to extract JSON from the content
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            try:
                parsed = json.loads(json_match.group(0))
            except json.JSONDecodeError:
                parsed = {}
        else:
            parsed = {}

    email_msg = (parsed.get("email_message") or "").strip()
    social_msg = (parsed.get("social_message") or "").strip()
    call_msg = (parsed.get("call_script") or "").strip()

    # ---------- PASS 2: retry any messages that are out of range ----------
    async def _regenerate(kind: str, current: str) -> str:
        if MIN_CHARS <= len(current) <= MAX_CHARS:
            return current
        retry_prompt = (
            f"The {kind} you just wrote is {len(current)} characters long. "
            f"It MUST be between {MIN_CHARS} and {MAX_CHARS} characters. "
            f"Rewrite ONLY this {kind} so it falls in that range. "
            f"Keep the same tone and approach but adjust the length. "
            f"Return JSON: {{\"{kind}\": \"...\"}}"
        )
        try:
            retry_response = await execute_llm_payload({
                "model": DEEPSEEK_SCOUT_MODEL,
                "messages": [
                    {"role": "system", "content": "You are an expert copywriter. Respond with valid JSON only."},
                    {"role": "user", "content": retry_prompt},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.6,
            })
            retry_content = retry_response.get("choices", [{}])[0].get("message", {}).get("content", "{}")
            try:
                if retry_content.strip().startswith("{"):
                    retry_parsed = json.loads(retry_content)
                else:
                    m = re.search(r'\{[\s\S]*\}', retry_content)
                    retry_parsed = json.loads(m.group(0)) if m else {}
            except (json.JSONDecodeError, AttributeError):
                retry_parsed = {}
            return (retry_parsed.get(kind) or "").strip()
        except Exception as e:
            print(f"[OUTREACH] Regeneration failed for {kind}: {e}")
            return current

    # Only regenerate if significantly out of range (avoid wasting API calls for small misses)
    if len(email_msg) < MIN_CHARS - 50 or len(email_msg) > MAX_CHARS + 50:
        email_msg = await _regenerate("email_message", email_msg)
    if len(social_msg) < MIN_CHARS - 50 or len(social_msg) > MAX_CHARS + 50:
        social_msg = await _regenerate("social_message", social_msg)
    if len(call_msg) < MIN_CHARS - 50 or len(call_msg) > MAX_CHARS + 50:
        call_msg = await _regenerate("call_script", call_msg)

    # ---------- FINAL ENFORCEMENT: post-process to guarantee the window ----------
    email_final = _enforce_length(email_msg, "email")
    social_final = _enforce_length(social_msg, "social")
    call_final = _enforce_length(call_msg, "call")

    # Log any messages that needed post-processing (for monitoring)
    if email_final != email_msg:
        print(f"[OUTREACH] email_message post-processed: {len(email_msg)} → {len(email_final)} chars")
    if social_final != social_msg:
        print(f"[OUTREACH] social_message post-processed: {len(social_msg)} → {len(social_final)} chars")
    if call_final != call_msg:
        print(f"[OUTREACH] call_script post-processed: {len(call_msg)} → {len(call_final)} chars")

    return {
        "email_message": email_final,
        "social_message": social_final,
        "call_script": call_final,
    }
