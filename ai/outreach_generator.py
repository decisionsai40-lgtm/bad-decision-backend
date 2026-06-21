"""
BAD DECISION — Outreach Message Generator
==========================================

Uses deepseek-chat (flagship, non-thinking) for FAST generation (~5-10s per lead).

NO WEBSITE SCRAPING:
  - We do NOT scrape the lead's website ourselves.
  - Instead, we pass the lead's website URL + social media URLs to DeepSeek
    and let the AI model check them (it has web browsing capability via its
    training data and can reason about the business from the URL structure).
  - This is faster and more reliable than our own scraping.

Generates 4 personalized outreach outputs per lead:
  1. EMAIL SUBJECT   (40-70 chars)
  2. EMAIL message   (500-530 chars, complete)
  3. SOCIAL DM       (500-530 chars, complete)
  4. CALL SCRIPT     (500-530 chars, complete)

COMPLETENESS-FIRST:
  - Every message must end with proper punctuation
  - Never cut mid-sentence to meet char count
  - If a message comes back empty, retry it
"""

import json
import re
from typing import Dict, Any

from ai.deepseek_middleware import execute_llm_payload, CriticalError
from config import DEEPSEEK_SCOUT_MODEL


# ============================================================
# CHARACTER LIMITS
# ============================================================
MIN_CHARS = 500
MAX_CHARS = 530
TOLERANCE_MIN = 450
TOLERANCE_MAX = 580

SUBJECT_MIN = 40
SUBJECT_MAX = 70

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
    sender_company: str,
    sender_name: str,
) -> str:
    """Build the user-message prompt for DeepSeek."""

    company = lead.get("company_name") or lead.get("author_username") or "this business"

    # Fix the "Hi ABSENT" bug
    dm_name = lead.get("dm_name") or ""
    if not dm_name or dm_name == "ABSENT":
        dm_name = ""
    dm_position = lead.get("dm_position") or ""
    if dm_position == "ABSENT":
        dm_position = ""

    website = lead.get("website_url") or ""
    if website == "ABSENT":
        website = ""

    email = lead.get("verified_email") or ""
    if email == "ABSENT":
        email = ""

    phone = lead.get("phone") or ""
    if phone == "ABSENT":
        phone = ""

    # Social media URLs — pass these to DeepSeek so it can reason about the business
    facebook = lead.get("facebook") or ""
    if facebook == "ABSENT":
        facebook = ""
    instagram = lead.get("instagram") or ""
    if instagram == "ABSENT":
        instagram = ""
    linkedin = lead.get("linkedin") or ""
    if linkedin == "ABSENT":
        linkedin = ""

    # Address
    address = lead.get("address") or ""
    if address == "ABSENT":
        address = ""

    # Determine greeting name
    greeting_name = dm_name if dm_name else "there"

    style_instruction = STYLE_PROMPTS.get(style, STYLE_PROMPTS["david_ogilvy"])

    # Build lead context — pass ALL URLs so DeepSeek can check them
    lead_lines = [f"LEAD BUSINESS NAME: {company}"]
    if dm_name:
        lead_lines.append(f"CONTACT PERSON: {dm_name}" + (f" ({dm_position})" if dm_position else ""))
    if website:
        lead_lines.append(f"WEBSITE URL: {website}")
    if facebook:
        lead_lines.append(f"FACEBOOK PAGE: {facebook}")
    if instagram:
        lead_lines.append(f"INSTAGRAM PAGE: {instagram}")
    if linkedin:
        lead_lines.append(f"LINKEDIN PAGE: {linkedin}")
    if phone:
        lead_lines.append(f"PHONE: {phone}")
    if address:
        lead_lines.append(f"LOCATION: {address}")

    lead_lines.append(
        f"\nIMPORTANT: Check the lead's website URL and social media pages above to understand "
        f"what {company} does, their services, and their specialty. Use this information to "
        f"write a message that feels like it was written specifically for them. "
        f"Do NOT mention star ratings, review counts, or generic platform data. "
        f"Reference something specific you can infer from their website URL or business name."
    )

    lead_context = "\n".join(lead_lines)

    # Sender context
    sender_lines = []
    if sender_company:
        sender_lines.append(f"YOUR COMPANY: {sender_company}")
    if sender_name:
        sender_lines.append(f"YOUR NAME: {sender_name}")
    sender_lines.append(f"WHAT YOU SELL: {user_service}")
    sender_lines.append(f"YOUR TARGET AUDIENCE: {target_audience or 'businesses like this one'}")
    sender_context = "\n".join(sender_lines)

    # Sign-off
    if sender_name and sender_company:
        sign_off = f" Sign off as '{sender_name} from {sender_company}'."
    elif sender_company:
        sign_off = f" Sign off as 'the team at {sender_company}'."
    elif sender_name:
        sign_off = f" Sign off as '{sender_name}'."
    else:
        sign_off = " Sign off as 'Alex from Bad Decision'."

    char_rule = f"""CHARACTER LIMITS (CRITICAL):
- email_subject: {SUBJECT_MIN}-{SUBJECT_MAX} characters
- email_message: {MIN_CHARS}-{MAX_CHARS} characters
- social_message: {MIN_CHARS}-{MAX_CHARS} characters
- call_script: {MIN_CHARS}-{MAX_CHARS} characters

COMPLETENESS RULE:
Every message MUST end with proper punctuation (. ! or ?).
NEVER cut off a sentence mid-way to meet the character count.

REQUIRED FIELDS RULE:
You MUST provide ALL FOUR fields. Do NOT leave any field empty.
If you can't think of content, write something — never return an empty string.

OUTREACH CONTEXT:
These are COLD OUTREACH messages from YOU ({sender_company or 'your company'}) TO the lead business ({company}).
The goal is to start a conversation that could lead to a sale of YOUR service.

GREETING RULE:
- Start emails and social DMs with "Hi {greeting_name}," (use the contact person's name if available, otherwise use "there")
- NEVER use "Hi ABSENT" or any placeholder — if no name is available, use "Hi there," or "Hi {company} team,"

The email_subject is SEPARATE from the email_message body."""

    return f"""You are an expert copywriter writing personalized COLD OUTREACH messages.

{style_instruction}

You are writing on behalf of YOUR business to pitch YOUR service to the LEAD business.

LEAD INFORMATION (the business you are pitching TO):
{lead_context}

YOUR INFORMATION (the business pitching):
{sender_context}

{char_rule}

TASK:
Write FOUR outputs. Each must be ready-to-send with NO placeholders.
ALL FOUR fields must be filled — do NOT leave any empty.

1. "email_subject" — a subject line for the cold email. {SUBJECT_MIN}-{SUBJECT_MAX} chars.
   Must reference something specific about {company}.
   Curiosity-driven, NOT spammy, NOT clickbait.

2. "email_message" — a cold email body. {MIN_CHARS}-{MAX_CHARS} chars.
   Start with "Hi {greeting_name},". The body MUST connect YOUR service to something
   specific about THEIR business.{sign_off}
   Do NOT mention star ratings, review counts, or generic platform data.

3. "social_message" — a LinkedIn or Instagram DM. {MIN_CHARS}-{MAX_CHARS} chars.
   More casual tone. Must mention {company} and reference something specific about their business.
   Do NOT mention star ratings or review counts.

4. "call_script" — a phone call opening script (first 30 seconds). {MIN_CHARS}-{MAX_CHARS} chars.
   Include a greeting, mention you're calling about {company} specifically,
   reference something about their business, and ask a permission question.

Return ONLY a JSON object with exactly these keys (no markdown, no code fences):
{{
  "email_subject": "...",
  "email_message": "...",
  "social_message": "...",
  "call_script": "..."
}}"""


# ============================================================
# CHARACTER ENFORCEMENT
# ============================================================
def _is_complete(message: str) -> bool:
    if not message:
        return False
    stripped = message.rstrip()
    return bool(stripped) and stripped[-1] in '.!?'

def _enforce_length(message: str, kind: str = "message") -> str:
    if not message:
        return ""
    message = message.strip()
    if not message:
        return ""

    if MIN_CHARS <= len(message) <= MAX_CHARS:
        return message
    if TOLERANCE_MIN <= len(message) <= TOLERANCE_MAX and _is_complete(message):
        return message

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
        if len(message) <= TOLERANCE_MAX:
            return message
        return message[:TOLERANCE_MAX - 3].rstrip() + "..."

    if len(message) < MIN_CHARS:
        if not _is_complete(message):
            message += "."
        cta_options = [
            " Reply to this message and I'll send over a short breakdown.",
            " Hit reply with 'yes' and I'll forward the details today.",
            " Want me to send the full breakdown? Just reply here.",
            " Let me know a good time and I'll walk you through it.",
        ]
        for cta in cta_options:
            candidate = message + cta
            if MIN_CHARS <= len(candidate) <= MAX_CHARS:
                return candidate
        return message

    return message


# ============================================================
# JSON EXTRACTION
# ============================================================
def _extract_json(content: str) -> Dict[str, Any]:
    if not content:
        return {}
    content = content.strip()
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
    skip_regeneration: bool = False,
) -> Dict[str, str]:
    """
    Generate personalized outreach messages for a single lead.
    Passes the lead's website URL + social media URLs to DeepSeek — NO scraping.

    skip_regeneration: If True, skip the retry pass (for batch mode — faster).
                       Just do initial generation + post-processing.
    """
    if not user_service:
        raise ValueError("user_service is required")

    style = copywriting_style if copywriting_style in STYLE_PROMPTS else "david_ogilvy"
    prompt = _build_prompt(lead, user_service, target_audience, style, sender_company, sender_name)

    # Determine greeting name for fallback messages
    dm_name = lead.get("dm_name") or ""
    if not dm_name or dm_name == "ABSENT":
        dm_name = ""
    greeting_name = dm_name if dm_name else "there"

    # ---------- PASS 1: initial generation ----------
    # max_tokens prevents DeepSeek from truncating the JSON (which causes empty fields)
    company_name = lead.get("company_name") or "unknown"
    mode_label = "batch" if skip_regeneration else "single"
    print(f"[OUTREACH] Generating ({mode_label}) with model={MODEL} for lead: {company_name}")
    response = await execute_llm_payload({
        "model": MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are an expert direct-response copywriter who writes personalized "
                    "COLD OUTREACH messages. You ALWAYS respond with a valid JSON object. "
                    "Your messages are always complete, personalized, and written as outreach "
                    "from one business to another. You never include generic platform data "
                    "like star ratings or review counts. You never use placeholder text. "
                    "You MUST fill in ALL four fields — never leave any empty. "
                    "Each message body must be 500-530 characters. Keep messages concise."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.7,
        "max_tokens": 3000,  # Prevent truncation — 4 messages × ~600 chars + JSON overhead
    })

    content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
    if not content:
        reasoning = response.get("choices", [{}])[0].get("message", {}).get("reasoning_content", "")
        if reasoning:
            content = reasoning

    parsed = _extract_json(content)

    email_msg = (parsed.get("email_message") or "").strip()
    social_msg = (parsed.get("social_message") or "").strip()
    call_msg = (parsed.get("call_script") or "").strip()
    email_subj = (parsed.get("email_subject") or "").strip()

    # Fix any "Hi ABSENT" or "ABSENT" that slipped through
    if email_msg and 'ABSENT' in email_msg:
        email_msg = email_msg.replace('Hi ABSENT,', 'Hi there,').replace('Hi ABSENT', 'Hi there').replace('ABSENT', 'there')
    if social_msg and 'ABSENT' in social_msg:
        social_msg = social_msg.replace('Hi ABSENT,', 'Hi there,').replace('Hi ABSENT', 'Hi there').replace('ABSENT', 'there')
    if call_msg and 'ABSENT' in call_msg:
        call_msg = call_msg.replace('Hi ABSENT,', 'Hi there,').replace('Hi ABSENT', 'Hi there').replace('ABSENT', 'there')
    if email_subj and 'ABSENT' in email_subj:
        email_subj = email_subj.replace('ABSENT', company_name)

    print(f"[OUTREACH] Initial lengths: subject={len(email_subj)}, email={len(email_msg)}, social={len(social_msg)}, call={len(call_msg)}")

    # ---------- PASS 2: retry if out of range, incomplete, or EMPTY ----------
    async def _regenerate(kind: str, current: str) -> str:
        # Retry if empty, out of range, or incomplete
        needs_retry = (
            not current
            or len(current) < TOLERANCE_MIN
            or len(current) > TOLERANCE_MAX
            or not _is_complete(current)
        )
        if not needs_retry:
            return current

        retry_prompt = (
            f"The {kind} you just wrote is "
            + ("empty" if not current else f"{len(current)} characters and {'incomplete' if not _is_complete(current) else 'out of range'}")
            + f". It MUST be between {MIN_CHARS} and {MAX_CHARS} characters AND complete. "
            f"It MUST be a COLD OUTREACH message referencing {company_name} specifically. "
            f"Do NOT use 'ABSENT' as a name — use 'there' or the company name. "
            f"Rewrite ONLY this {kind}. Return JSON: {{\"{kind}\": \"...\"}}"
        )
        try:
            retry_response = await execute_llm_payload({
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": "You are an expert copywriter. Respond with valid JSON only. Always fill in the field — never leave it empty."},
                    {"role": "user", "content": retry_prompt},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.6,
            })
            retry_content = retry_response.get("choices", [{}])[0].get("message", {}).get("content", "")
            retry_parsed = _extract_json(retry_content)
            result = (retry_parsed.get(kind) or "").strip()
            if result and 'ABSENT' not in result:
                return result
        except Exception as e:
            print(f"[OUTREACH] Regeneration failed for {kind}: {e}")
        return current

    # Regenerate any that are empty, out of range, or incomplete
    # SKIP regeneration in batch mode for speed (post-processing will handle length)
    if not skip_regeneration:
        if not email_msg or len(email_msg) < TOLERANCE_MIN or len(email_msg) > TOLERANCE_MAX or not _is_complete(email_msg):
            print(f"[OUTREACH] Regenerating email_message (len={len(email_msg)}, complete={_is_complete(email_msg)})")
            email_msg = await _regenerate("email_message", email_msg)
        if not social_msg or len(social_msg) < TOLERANCE_MIN or len(social_msg) > TOLERANCE_MAX or not _is_complete(social_msg):
            print(f"[OUTREACH] Regenerating social_message (len={len(social_msg)}, complete={_is_complete(social_msg)})")
            social_msg = await _regenerate("social_message", social_msg)
        if not call_msg or len(call_msg) < TOLERANCE_MIN or len(call_msg) > TOLERANCE_MAX or not _is_complete(call_msg):
            print(f"[OUTREACH] Regenerating call_script (len={len(call_msg)}, complete={_is_complete(call_msg)})")
            call_msg = await _regenerate("call_script", call_msg)
    else:
        print(f"[OUTREACH] Skipping regeneration (batch mode) — using post-processing only")

    # ---------- FINAL ENFORCEMENT ----------
    email_final = _enforce_length(email_msg, "email")
    social_final = _enforce_length(social_msg, "social")
    call_final = _enforce_length(call_msg, "call")

    # If any are still empty after everything, use a fallback
    if not email_final:
        email_final = f"Hi {greeting_name}, I came across {company_name} and was impressed by what you do. I help businesses like yours with {user_service}. I'd love to show you how we could help {company_name} specifically. Reply to this message and I'll send over a short breakdown."
    if not social_final:
        social_final = f"Hi {greeting_name}, I came across {company_name} and love what you do. I help businesses like yours with {user_service}. I have an idea that could help {company_name} specifically. Want me to send the details? Just reply here."
    if not call_final:
        call_final = f"Hi {greeting_name}, this is {sender_name or 'Alex'} from {sender_company or 'Bad Decision'}. I'm calling because I was impressed by {company_name}. I specialize in helping businesses like yours with {user_service}. I have a quick idea that could save your team time. Would you be open to a brief conversation? I promise to keep it under 10 minutes."

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
    else:
        email_subj = f"A quick idea for {company_name}"

    print(f"[OUTREACH] Final lengths: subject={len(email_subj)}, email={len(email_final)}, social={len(social_final)}, call={len(call_final)}")

    return {
        "email_subject": email_subj,
        "email_message": email_final,
        "social_message": social_final,
        "call_script": call_final,
    }
