"""
BAD DECISION — Outreach Message Generator
==========================================

Uses deepseek-chat (flagship, non-thinking mode) for FAST generation.
deepseek-reasoner was too slow (30-60s per lead). deepseek-chat is the
flagship model without the reasoning step — much faster (~5-10s per lead)
while still producing high-quality output.

Generates 4 personalized outreach outputs per lead:
  1. EMAIL SUBJECT   (40-70 chars)
  2. EMAIL message   (500-530 chars, complete)
  3. SOCIAL DM       (500-530 chars, complete)
  4. CALL SCRIPT     (500-530 chars, complete)

COMPLETENESS-FIRST CHARACTER ENFORCEMENT:
  - The #1 priority is that every message is COMPLETE (ends with proper punctuation).
  - The #2 priority is hitting the 500-530 character window.
  - We NEVER hard-cut a message mid-sentence to meet the char count.

PERSONALIZATION:
  - Uses the sender's company name, service, and target audience
  - References the lead's specific business name, website, and decision maker
  - Messages are written to feel like they were written FOR this specific lead
"""

import json
import re
from typing import Dict, Any

from ai.deepseek_middleware import execute_llm_payload, CriticalError
from config import DEEPSEEK_SCOUT_MODEL


# ============================================================
# CHARACTER LIMITS — with completeness tolerance
# ============================================================
MIN_CHARS = 500
MAX_CHARS = 530
TOLERANCE_MIN = 450
TOLERANCE_MAX = 580

SUBJECT_MIN = 40
SUBJECT_MAX = 70

# Use deepseek-chat (flagship, non-thinking) for FAST generation.
# deepseek-reasoner was too slow (30-60s per lead). deepseek-chat is ~5-10s.
MODEL = DEEPSEEK_SCOUT_MODEL


# ============================================================
# COPYWRITING STYLE PROMPTS
# ============================================================
STYLE_PROMPTS: Dict[str, str] = {
    "dan_kennedy": (
        "Write in Dan Kennedy's no-nonsense direct-response style: "
        "punchy opener, a specific pain point, a concrete promise, "
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
def _build_prompt(
    lead: Dict[str, Any],
    user_service: str,
    target_audience: str,
    style: str,
    sender_company: str = "",
    sender_name: str = "",
) -> str:
    """Build the user-message prompt for DeepSeek."""
    # Lead context — make it VERY specific so messages feel personalized
    company = lead.get("company_name") or lead.get("author_username") or "this business"
    dm_name = lead.get("dm_name") or ""
    dm_position = lead.get("dm_position") or ""
    website = lead.get("website_url") or ""
    email = lead.get("verified_email") or ""
    phone = lead.get("phone") or ""
    address = lead.get("address") or ""
    category = lead.get("category") or ""
    rating = lead.get("rating")
    review_count = lead.get("review_count")
    intent = lead.get("intent_text") or lead.get("intent_level") or ""

    style_instruction = STYLE_PROMPTS.get(style, STYLE_PROMPTS["david_ogilvy"])

    # Build rich lead context so the AI can write a SPECIFIC, relevant message
    lead_details = [f"BUSINESS NAME: {company}"]
    if dm_name:
        lead_details.append(f"CONTACT PERSON: {dm_name}" + (f", {dm_position}" if dm_position else ""))
    if website:
        lead_details.append(f"WEBSITE: {website}")
    if email and email != "ABSENT":
        lead_details.append(f"EMAIL: {email}")
    if phone and phone != "ABSENT":
        lead_details.append(f"PHONE: {phone}")
    if address and address != "ABSENT":
        lead_details.append(f"LOCATION: {address}")
    if category and category != "ABSENT":
        lead_details.append(f"BUSINESS TYPE: {category}")
    if rating:
        lead_details.append(f"RATING: {rating} stars" + (f" ({review_count} reviews)" if review_count else ""))
    if intent and intent != "ABSENT":
        lead_details.append(f"INTENT SIGNAL: {intent}")

    lead_context = "\n".join(lead_details)

    # Sender context — include company name for personalization
    sender_lines = [f"SENDER'S SERVICE: {user_service}"]
    if sender_company:
        sender_lines.append(f"SENDER'S COMPANY NAME: {sender_company}")
    if sender_name:
        sender_lines.append(f"SENDER'S NAME: {sender_name}")
    sender_lines.append(f"SENDER'S TARGET AUDIENCE: {target_audience or 'businesses like this one'}")
    sender_context = "\n".join(sender_lines)

    # Sign-off instruction
    sign_off = f' Sign off as "{sender_name or "Alex"} from {sender_company or "Bad Decision"}".' if (sender_company or sender_name) else ' Sign off as "Alex from Bad Decision".'

    char_rule = f"""CHARACTER LIMITS (CRITICAL):
- email_subject: {SUBJECT_MIN}-{SUBJECT_MAX} characters
- email_message: {MIN_CHARS}-{MAX_CHARS} characters
- social_message: {MIN_CHARS}-{MAX_CHARS} characters
- call_script: {MIN_CHARS}-{MAX_CHARS} characters

COMPLETENESS RULE (MOST IMPORTANT):
Every message MUST be COMPLETE — end with proper punctuation (. ! or ?).
NEVER cut off a sentence mid-way to meet the character count.

PERSONALIZATION RULE (CRITICAL):
Every message MUST reference the lead's SPECIFIC business by name ({company}).
The message must make it clear that it was written for {company} specifically,
not a generic template. Reference their website, location, or business type
when relevant. Do NOT write generic messages that could apply to any business.

The email_subject is SEPARATE from the email_message body."""

    return f"""You are an expert copywriter writing personalized outreach messages.

{style_instruction}

LEAD INFORMATION (the business you are writing TO):
{lead_context}

YOUR INFORMATION (the business you are writing FROM):
{sender_context}

{char_rule}

TASK:
Write FOUR outputs for this lead. Each must be ready-to-send with NO placeholders
(no [Your Name], no [Company], etc.) — use the actual data above.

1. "email_subject" — a subject line for the cold email. {SUBJECT_MIN}-{SUBJECT_MAX} chars.
   Must reference {company} or their industry specifically. Curiosity-driven, NOT spammy.

2. "email_message" — a cold email body (NOT including the subject). {MIN_CHARS}-{MAX_CHARS} chars.
   Start with a greeting using "{dm_name or "there"}". The body MUST connect YOUR service
   to THEIR specific business situation. Reference their business name, website, or location.{sign_off}

3. "social_message" — a LinkedIn or Instagram DM. {MIN_CHARS}-{MAX_CHARS} chars.
   More casual tone. Must mention {company} by name. Reference something specific
   about their business (website, location, or business type).

4. "call_script" — a phone call opening script (first 30 seconds). {MIN_CHARS}-{MAX_CHARS} chars.
   Include a greeting, mention you're calling about {company} specifically,
   give a reason for calling tied to their business, and ask a permission question.

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
    """Check if a message ends with proper punctuation."""
    if not message:
        return False
    stripped = message.rstrip()
    if not stripped:
        return False
    return stripped[-1] in '.!?'

def _enforce_length(message: str, kind: str = "message") -> str:
    """Force a message toward the [MIN_CHARS, MAX_CHARS] window. Never cut mid-sentence."""
    if not message:
        return ""

    message = message.strip()
    if not message:
        return ""

    if MIN_CHARS <= len(message) <= MAX_CHARS:
        return message

    if TOLERANCE_MIN <= len(message) <= TOLERANCE_MAX and _is_complete(message):
        return message

    # TOO LONG → trim at sentence boundary
    if len(message) > MAX_CHARS:
        cut_zone = message[:MAX_CHARS]
        boundary = -1
        for punct in ['. ', '! ', '? ', '."', '!"', '?"', '.\n', '!\n', '?\n']:
            idx = cut_zone.rfind(punct)
            if idx > boundary:
                boundary = idx

        if boundary >= TOLERANCE_MIN - 50:
            trimmed = message[:boundary + 1].rstrip()
            if TOLERANCE_MIN <= len(trimmed) <= TOLERANCE_MAX:
                return trimmed
            # Try extending to next sentence boundary
            next_boundary = -1
            for punct in ['. ', '! ', '? ']:
                idx = message.find(punct, boundary + 1)
                if idx != -1 and (next_boundary == -1 or idx < next_boundary):
                    next_boundary = idx
            if next_boundary != -1 and next_boundary < TOLERANCE_MAX:
                extended = message[:next_boundary + 1].rstrip()
                if len(extended) <= TOLERANCE_MAX:
                    return extended
            if len(message) <= TOLERANCE_MAX:
                return message
            return message[:TOLERANCE_MAX - 3].rstrip() + "..."
        else:
            if len(message) <= TOLERANCE_MAX:
                return message
            return message[:TOLERANCE_MAX - 3].rstrip() + "..."

    # TOO SHORT → append CTA
    if len(message) < MIN_CHARS:
        if not _is_complete(message):
            message += "."

        cta_options = [
            " Reply to this message and I'll send over a short breakdown.",
            " Hit reply with 'yes' and I'll forward the details today.",
            " Want me to send the full breakdown? Just reply here.",
            " Let me know a good time and I'll walk you through it.",
            " Reply 'send it' and I'll forward everything you need.",
        ]
        for cta in cta_options:
            candidate = message + cta
            if MIN_CHARS <= len(candidate) <= MAX_CHARS:
                return candidate
        short_ctas = [" Reply to learn more.", " Hit reply with 'yes'.", " Want the details? Reply here."]
        for cta in short_ctas:
            candidate = message + cta
            if TOLERANCE_MIN <= len(candidate) <= TOLERANCE_MAX:
                return candidate
        return message

    return message


# ============================================================
# JSON EXTRACTION
# ============================================================
def _extract_json(content: str) -> Dict[str, Any]:
    """Extract a JSON object from the model's response."""
    if not content:
        return {}

    content = content.strip()
    # Strip markdown code fences
    if content.startswith("```"):
        lines = content.split("\n")
        if lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = "\n".join(lines).strip()

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

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
    sender_company: str = "",
    sender_name: str = "",
) -> Dict[str, str]:
    """
    Generate personalized outreach messages for a single lead.
    Uses deepseek-chat (flagship, non-thinking) for fast generation.
    """
    if not user_service:
        raise ValueError("user_service is required")

    style = copywriting_style if copywriting_style in STYLE_PROMPTS else "david_ogilvy"
    prompt = _build_prompt(lead, user_service, target_audience, style, sender_company, sender_name)

    # ---------- PASS 1: initial generation ----------
    # deepseek-chat supports response_format: json_object for reliable JSON output
    print(f"[OUTREACH] Generating with model={MODEL} for lead: {lead.get('company_name', 'unknown')}")
    response = await execute_llm_payload({
        "model": MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are an expert direct-response copywriter who writes personalized "
                    "outreach messages. You ALWAYS respond with a valid JSON object. "
                    "Your messages are always complete and specific to each lead. "
                    "You hit the target character count by adjusting detail and wording, "
                    "never by truncating."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.7,
    })

    content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
    if not content:
        print(f"[OUTREACH] WARNING: Empty content from model.")
        reasoning = response.get("choices", [{}])[0].get("message", {}).get("reasoning_content", "")
        if reasoning:
            content = reasoning

    parsed = _extract_json(content)

    email_msg = (parsed.get("email_message") or "").strip()
    social_msg = (parsed.get("social_message") or "").strip()
    call_msg = (parsed.get("call_script") or "").strip()
    email_subj = (parsed.get("email_subject") or "").strip()

    print(f"[OUTREACH] Initial lengths: subject={len(email_subj)}, email={len(email_msg)}, social={len(social_msg)}, call={len(call_msg)}")

    # ---------- PASS 2: retry if significantly out of range or incomplete ----------
    async def _regenerate(kind: str, current: str) -> str:
        if TOLERANCE_MIN <= len(current) <= TOLERANCE_MAX and _is_complete(current):
            return current
        if not current:
            return current

        company_name = lead.get("company_name") or "this business"
        retry_prompt = (
            f"The {kind} you just wrote is {len(current)} characters long and "
            f"{'incomplete' if not _is_complete(current) else 'out of range'}. "
            f"It MUST be between {MIN_CHARS} and {MAX_CHARS} characters AND complete. "
            f"It MUST reference {company_name} specifically. "
            f"Rewrite ONLY this {kind}. Return JSON: {{\"{kind}\": \"...\"}}"
        )
        try:
            retry_response = await execute_llm_payload({
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": "You are an expert copywriter. Respond with valid JSON only."},
                    {"role": "user", "content": retry_prompt},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.6,
            })
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

    # ---------- FINAL ENFORCEMENT ----------
    email_final = _enforce_length(email_msg, "email")
    social_final = _enforce_length(social_msg, "social")
    call_final = _enforce_length(call_msg, "call")

    if email_final != email_msg:
        print(f"[OUTREACH] email_message post-processed: {len(email_msg)} → {len(email_final)} chars")
    if social_final != social_msg:
        print(f"[OUTREACH] social_message post-processed: {len(social_msg)} → {len(social_final)} chars")
    if call_final != call_msg:
        print(f"[OUTREACH] call_script post-processed: {len(call_msg)} → {len(call_final)} chars")

    # Subject line enforcement
    if email_subj:
        if len(email_subj) > SUBJECT_MAX:
            cut = email_subj[:SUBJECT_MAX]
            last_space = cut.rfind(" ")
            if last_space > SUBJECT_MIN:
                email_subj = cut[:last_space].rstrip()
            else:
                email_subj = cut.rstrip()
        elif len(email_subj) < SUBJECT_MIN and len(email_subj) > 0:
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
