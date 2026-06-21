"""
BAD DECISION — Outreach Message Generator
==========================================

Generates 4 personalized outreach outputs per lead:
  1. EMAIL SUBJECT   (40-70 chars)
  2. EMAIL message   (500-530 chars, complete)
  3. SOCIAL DM       (500-530 chars, complete)
  4. CALL SCRIPT     (500-530 chars, complete)

USES THE ADVANCED MODEL (deepseek-reasoner):
  - deepseek-reasoner can reason through the message structure before writing,
    producing more complete, coherent messages that don't get cut off.
  - It does NOT support response_format: json_object, so we parse JSON manually.

COMPLETENESS-FIRST CHARACTER ENFORCEMENT:
  - The #1 priority is that every message is COMPLETE (ends with proper punctuation,
    no mid-sentence cuts, no trailing "..." unless intentional).
  - The #2 priority is hitting the 500-530 character window.
  - If a message is slightly under/over but complete, we accept it (450-580 tolerance).
  - If a message is significantly out of range, we regenerate ONCE with explicit feedback.
  - We NEVER hard-cut a message mid-sentence to meet the char count — that creates
    the "incomplete message" problem the user reported.
  - If too long: trim at a sentence boundary. If that puts us under 450, keep the
    original complete message instead (completeness > exact char count).
  - If too short: append a relevant call-to-action sentence that fits naturally.

COPYWRITING STYLES (6):
  dan_kennedy, donald_miller, ray_edwards, david_ogilvy, jay_abraham, gary_halbert

NO PLACEHOLDERS:
  Messages must be ready-to-send — no [Your Name], no [Company], etc.
"""

import json
import re
from typing import Dict, Any

from ai.deepseek_middleware import execute_llm_payload, CriticalError
from config import DEEPSEEK_SCOUT_MODEL, DEEPSEEK_SCHOLAR_MODEL


# ============================================================
# CHARACTER LIMITS — with completeness tolerance
# ============================================================
# Target window: 500-530 chars
# Tolerance: 450-580 chars (accept if the message is complete and close to range)
MIN_CHARS = 500
MAX_CHARS = 530
TOLERANCE_MIN = 450   # below this, we try to extend
TOLERANCE_MAX = 580   # above this, we try to trim

# Subject line limits
SUBJECT_MIN = 40
SUBJECT_MAX = 70

# Use the advanced reasoning model for better message quality.
# deepseek-reasoner can think through the message structure before writing,
# which produces more complete, coherent messages.
USE_REASONER = True
MODEL = DEEPSEEK_SCHOLAR_MODEL if USE_REASONER else DEEPSEEK_SCOUT_MODEL


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

    char_rule = f"""CHARACTER LIMITS (CRITICAL):
- email_subject: {SUBJECT_MIN}-{SUBJECT_MAX} characters
- email_message: {MIN_CHARS}-{MAX_CHARS} characters
- social_message: {MIN_CHARS}-{MAX_CHARS} characters
- call_script: {MIN_CHARS}-{MAX_CHARS} characters

COMPLETENESS RULE (MOST IMPORTANT):
Every message MUST be a COMPLETE, finished thought. It must end with proper
punctuation (period, question mark, or exclamation). NEVER cut off a sentence
mid-way to meet the character count. If you're running long, remove a detail
or tighten the wording — do NOT truncate. If you're running short, add a
specific detail or a call-to-action sentence.

The email_subject is SEPARATE from the email_message body. Do NOT include
the subject line inside the email_message field."""

    return f"""You are an expert copywriter writing personalized outreach messages.

{style_instruction}

{lead_context}

{sender_context}

{char_rule}

TASK:
Write FOUR outputs for this lead. Each must be ready-to-send with NO placeholders
(no [Your Name], no [Company], etc.) — use the actual lead data above.

1. "email_subject" — a subject line for the cold email. {SUBJECT_MIN}-{SUBJECT_MAX} chars.
   Curiosity-driven, specific to the lead, NOT spammy. No clickbait.

2. "email_message" — a cold email body (NOT including the subject). {MIN_CHARS}-{MAX_CHARS} chars.
   Include a greeting using the DM name if known, a body that connects the sender's
   service to the lead's situation, and a soft call-to-action. Sign off as
   "Alex from Bad Decision" (or omit if it pushes over the limit).

3. "social_message" — a LinkedIn or Instagram DM. {MIN_CHARS}-{MAX_CHARS} chars.
   More casual, shorter sentences. Can reference the lead's website or business.

4. "call_script" — a phone call opening script (first 30 seconds). {MIN_CHARS}-{MAX_CHARS} chars.
   Include a greeting, a reason for calling tied to the lead's business, and a
   permission-based question to continue the conversation.

Return ONLY a JSON object with exactly these keys (no markdown, no code fences):
{{
  "email_subject": "...",
  "email_message": "...",
  "social_message": "...",
  "call_script": "..."
}}"""


# ============================================================
# CHARACTER ENFORCEMENT — completeness-first
# ============================================================
def _is_complete(message: str) -> bool:
    """Check if a message ends with proper punctuation (is a complete thought)."""
    if not message:
        return False
    stripped = message.rstrip()
    if not stripped:
        return False
    last_char = stripped[-1]
    return last_char in '.!?'

def _enforce_length(message: str, kind: str = "message") -> str:
    """
    Force a message toward the [MIN_CHARS, MAX_CHARS] window.
    COMPLETENESS IS PRIORITY #1 — never cut a message mid-sentence.

    Strategy:
    - If already in [MIN_CHARS, MAX_CHARS]: return as-is.
    - If in tolerance range [TOLERANCE_MIN, TOLERANCE_MAX] and complete: return as-is.
    - If too long (> TOLERANCE_MAX): trim at sentence boundary. If trimmed version
      is < TOLERANCE_MIN, keep the original (completeness > char count).
    - If too short (< TOLERANCE_MIN): append a relevant CTA sentence.
    """
    if not message:
        message = ""

    message = message.strip()
    if not message:
        return ""

    # If already in target window, return as-is
    if MIN_CHARS <= len(message) <= MAX_CHARS:
        return message

    # If in tolerance range AND complete, accept it
    if TOLERANCE_MIN <= len(message) <= TOLERANCE_MAX and _is_complete(message):
        return message

    # TOO LONG → trim at sentence boundary (never mid-sentence)
    if len(message) > MAX_CHARS:
        # Find the last sentence boundary at or before MAX_CHARS
        cut_zone = message[:MAX_CHARS]
        boundary = -1
        # Look for sentence-ending punctuation followed by space or end
        for punct in ['. ', '! ', '? ', '."', '!"', '?"', '.\n', '!\n', '?\n']:
            idx = cut_zone.rfind(punct)
            if idx > boundary:
                boundary = idx

        if boundary >= TOLERANCE_MIN - 50:
            # Trim at the sentence boundary + include the punctuation
            trimmed = message[:boundary + 1].rstrip()
            if TOLERANCE_MIN <= len(trimmed) <= TOLERANCE_MAX:
                return trimmed
            # If trimmed is still too short, try extending to the next sentence
            # boundary beyond MAX_CHARS (up to TOLERANCE_MAX)
            next_boundary = message.find('. ', boundary + 1)
            if next_boundary == -1:
                next_boundary = message.find('! ', boundary + 1)
            if next_boundary == -1:
                next_boundary = message.find('? ', boundary + 1)
            if next_boundary != -1 and next_boundary < TOLERANCE_MAX:
                extended = message[:next_boundary + 1].rstrip()
                if len(extended) <= TOLERANCE_MAX:
                    return extended
            # If we can't find a good boundary, keep the original if it's within tolerance
            if len(message) <= TOLERANCE_MAX:
                return message
            # Last resort: hard cut at TOLERANCE_MAX with ellipsis (rare)
            return message[:TOLERANCE_MAX - 3].rstrip() + "..."
        else:
            # No good sentence boundary — keep original if within tolerance
            if len(message) <= TOLERANCE_MAX:
                return message
            # Hard cut at TOLERANCE_MAX with ellipsis
            return message[:TOLERANCE_MAX - 3].rstrip() + "..."

    # TOO SHORT → append a relevant CTA sentence
    if len(message) < MIN_CHARS:
        # First, make sure the existing message ends with punctuation
        if not _is_complete(message):
            message += "."

        cta_options = [
            " Reply to this message and I'll send over a short breakdown.",
            " Hit reply with 'yes' and I'll forward the details today.",
            " Want me to send the full breakdown? Just reply here.",
            " Let me know a good time and I'll walk you through it.",
            " Reply 'send it' and I'll forward everything you need.",
            " I'd love to hear what you think — just hit reply.",
            " Can I send you a quick 2-minute video showing exactly how?",
        ]
        for cta in cta_options:
            candidate = message + cta
            if MIN_CHARS <= len(candidate) <= MAX_CHARS:
                return candidate
        # If a single CTA overshoots, try shorter ones
        short_ctas = [
            " Reply to learn more.",
            " Hit reply with 'yes'.",
            " Want the details? Reply here.",
        ]
        for cta in short_ctas:
            candidate = message + cta
            if TOLERANCE_MIN <= len(candidate) <= TOLERANCE_MAX:
                return candidate
        # If still too short, return as-is (completeness > exact char count)
        return message

    return message


# ============================================================
# JSON EXTRACTION — handles reasoner model output
# ============================================================
def _extract_json(content: str) -> Dict[str, Any]:
    """
    Extract a JSON object from the model's response.
    The reasoner model may include reasoning text before the JSON,
    or wrap it in markdown code fences.
    """
    if not content:
        return {}

    # Strip markdown code fences if present
    content = content.strip()
    if content.startswith("```"):
        # Remove ```json or ``` prefix and trailing ```
        lines = content.split("\n")
        # Remove first line (```json or ```)
        if lines[0].strip().startswith("```"):
            lines = lines[1:]
        # Remove last line if it's just ```
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = "\n".join(lines).strip()

    # Try direct JSON parse
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    # Try to find a JSON object in the text
    json_match = re.search(r'\{[\s\S]*\}', content)
    if json_match:
        try:
            return json.loads(json_match.group(0))
        except json.JSONDecodeError:
            pass

    return {}


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
    Generate personalized outreach messages for a single lead.

    Uses the advanced deepseek-reasoner model for better quality and completeness.

    Returns:
        Dict with keys: email_subject, email_message, social_message, call_script
    """
    if not user_service:
        raise ValueError("user_service is required — tell the user to set up their service in Settings first.")

    style = copywriting_style if copywriting_style in STYLE_PROMPTS else "david_ogilvy"
    prompt = _build_prompt(lead, user_service, target_audience, style)

    # ---------- BUILD PAYLOAD ----------
    # deepseek-reasoner does NOT support response_format: json_object.
    # We rely on the prompt to instruct JSON output and parse manually.
    payload: Dict[str, Any] = {
        "model": MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are an expert direct-response copywriter who writes personalized "
                    "outreach messages. You ALWAYS respond with a valid JSON object. "
                    "Your messages are always complete — they never cut off mid-sentence. "
                    "You hit the target character count by adjusting detail and wording, "
                    "never by truncating."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.7,
    }

    # Only add response_format for non-reasoner models
    if not USE_REASONER:
        payload["response_format"] = {"type": "json_object"}

    # ---------- PASS 1: initial generation ----------
    print(f"[OUTREACH] Generating with model={MODEL} for lead: {lead.get('company_name', 'unknown')}")
    response = await execute_llm_payload(payload)

    # The reasoner model returns content in choices[0].message.content
    # (reasoning_content is separate and we don't need it)
    content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
    if not content:
        print(f"[OUTREACH] WARNING: Empty content from model. Full response keys: {list(response.keys())}")
        # Try to get reasoning_content as fallback (some models put output there)
        reasoning = response.get("choices", [{}])[0].get("message", {}).get("reasoning_content", "")
        if reasoning:
            content = reasoning
            print(f"[OUTREACH] Using reasoning_content as fallback ({len(content)} chars)")

    parsed = _extract_json(content)

    email_msg = (parsed.get("email_message") or "").strip()
    social_msg = (parsed.get("social_message") or "").strip()
    call_msg = (parsed.get("call_script") or "").strip()
    email_subj = (parsed.get("email_subject") or "").strip()

    print(f"[OUTREACH] Initial lengths: subject={len(email_subj)}, email={len(email_msg)}, social={len(social_msg)}, call={len(call_msg)}")

    # ---------- PASS 2: retry any messages that are significantly out of range ----------
    async def _regenerate(kind: str, current: str) -> str:
        """Regenerate a single message if it's way off the target length."""
        if TOLERANCE_MIN <= len(current) <= TOLERANCE_MAX and _is_complete(current):
            return current
        if not current:
            return current

        retry_prompt = (
            f"The {kind} you just wrote is {len(current)} characters long and "
            f"{'incomplete (cut off)' if not _is_complete(current) else 'out of range'}. "
            f"It MUST be between {MIN_CHARS} and {MAX_CHARS} characters AND complete "
            f"(end with proper punctuation). "
            f"Rewrite ONLY this {kind} so it falls in that range and is a complete thought. "
            f"Do NOT cut off mid-sentence. Adjust detail and wording to hit the target. "
            f"Return JSON: {{\"{kind}\": \"...\"}}"
        )
        try:
            retry_payload: Dict[str, Any] = {
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": "You are an expert copywriter. Respond with valid JSON only. Your messages are always complete."},
                    {"role": "user", "content": retry_prompt},
                ],
                "temperature": 0.6,
            }
            if not USE_REASONER:
                retry_payload["response_format"] = {"type": "json_object"}

            retry_response = await execute_llm_payload(retry_payload)
            retry_content = retry_response.get("choices", [{}])[0].get("message", {}).get("content", "")
            retry_parsed = _extract_json(retry_content)
            result = (retry_parsed.get(kind) or "").strip()
            if result:
                return result
        except Exception as e:
            print(f"[OUTREACH] Regeneration failed for {kind}: {e}")
        return current

    # Only regenerate if significantly out of range OR incomplete
    if (len(email_msg) < TOLERANCE_MIN or len(email_msg) > TOLERANCE_MAX or not _is_complete(email_msg)) and email_msg:
        print(f"[OUTREACH] Regenerating email_message (len={len(email_msg)}, complete={_is_complete(email_msg)})")
        email_msg = await _regenerate("email_message", email_msg)
    if (len(social_msg) < TOLERANCE_MIN or len(social_msg) > TOLERANCE_MAX or not _is_complete(social_msg)) and social_msg:
        print(f"[OUTREACH] Regenerating social_message (len={len(social_msg)}, complete={_is_complete(social_msg)})")
        social_msg = await _regenerate("social_message", social_msg)
    if (len(call_msg) < TOLERANCE_MIN or len(call_msg) > TOLERANCE_MAX or not _is_complete(call_msg)) and call_msg:
        print(f"[OUTREACH] Regenerating call_script (len={len(call_msg)}, complete={_is_complete(call_msg)})")
        call_msg = await _regenerate("call_script", call_msg)

    # ---------- FINAL ENFORCEMENT: completeness-first post-processing ----------
    email_final = _enforce_length(email_msg, "email")
    social_final = _enforce_length(social_msg, "social")
    call_final = _enforce_length(call_msg, "call")

    # Log any messages that needed post-processing (for monitoring)
    if email_final != email_msg:
        print(f"[OUTREACH] email_message post-processed: {len(email_msg)} → {len(email_final)} chars (complete={_is_complete(email_final)})")
    if social_final != social_msg:
        print(f"[OUTREACH] social_message post-processed: {len(social_msg)} → {len(social_final)} chars (complete={_is_complete(social_final)})")
    if call_final != call_msg:
        print(f"[OUTREACH] call_script post-processed: {len(call_msg)} → {len(call_final)} chars (complete={_is_complete(call_final)})")

    # Enforce subject line length (40-70 chars)
    if email_subj:
        if len(email_subj) > SUBJECT_MAX:
            # Trim at word boundary
            cut = email_subj[:SUBJECT_MAX]
            last_space = cut.rfind(" ")
            if last_space > SUBJECT_MIN:
                email_subj = cut[:last_space].rstrip()
            else:
                email_subj = cut.rstrip()
        elif len(email_subj) < SUBJECT_MIN and len(email_subj) > 0:
            # Try to extend with a power word
            extensions = [" — quick question", " (30 seconds)", " — ideas inside", " worth a look?"]
            for ext in extensions:
                if len(email_subj) + len(ext) <= SUBJECT_MAX and len(email_subj) + len(ext) >= SUBJECT_MIN:
                    email_subj += ext
                    break

    print(f"[OUTREACH] Final lengths: subject={len(email_subj)}, email={len(email_final)}, social={len(social_final)}, call={len(call_final)}")

    return {
        "email_subject": email_subj,
        "email_message": email_final,
        "social_message": social_final,
        "call_script": call_final,
    }
