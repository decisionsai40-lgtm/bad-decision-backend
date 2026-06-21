"""
BAD DECISION — Outreach Message Generator
==========================================

Uses deepseek-chat (flagship, non-thinking) for FAST generation (~5-10s per lead).

Generates 4 personalized outreach outputs per lead:
  1. EMAIL SUBJECT   (40-70 chars)
  2. EMAIL message   (500-530 chars, complete)
  3. SOCIAL DM       (500-530 chars, complete)
  4. CALL SCRIPT     (500-530 chars, complete)

PERSONALIZATION STRATEGY:
  - Scrapes the lead's website for unique details (services, about text, specialties)
  - Uses the sender's company name, service, and target audience from Settings
  - Messages are written as COLD OUTREACH from the sender's business TO the lead
  - References specific details found on the lead's website (not generic stuff like ratings)
  - Does NOT include review counts, star ratings, or generic platform data
  - Fixes "Hi ABSENT" bug: uses company name or "there" when DM name is missing

COMPLETENESS-FIRST:
  - Every message must end with proper punctuation
  - Never cut mid-sentence to meet char count
"""

import json
import re
from typing import Dict, Any

from ai.deepseek_middleware import execute_llm_payload, CriticalError
from config import DEEPSEEK_SCOUT_MODEL
from scraping.email_scraper import scrape_website_for_emails


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
# WEBSITE SCRAPER — extracts unique details for personalization
# ============================================================
async def _scrape_lead_website(website_url: str) -> str:
    """
    Scrape the lead's website to extract unique details for personalization.
    Returns a short summary of what the business does, their specialties, etc.
    """
    if not website_url or website_url == "ABSENT":
        return ""

    try:
        result = await scrape_website_for_emails(website_url)
        # The scraper returns emails, phone, social links.
        # We also need the page text to extract business details.
        # Let's do a quick fetch of the homepage text.
        import httpx
        from urllib.parse import urlparse
        from config import SOURCE_TIMEOUT

        domain = urlparse(website_url if website_url.startswith("http") else f"https://{website_url}").hostname or ""
        if domain.startswith("www."):
            domain = domain[4:]

        if not domain:
            return ""

        async with httpx.AsyncClient(timeout=SOURCE_TIMEOUT, follow_redirects=True) as client:
            response = await client.get(
                f"https://{domain}",
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            )
            if response.status_code == 200:
                html = response.text
                # Extract text content (strip HTML tags)
                text = re.sub(r'<script[^>]*>[\s\S]*?</script>', '', html, flags=re.IGNORECASE)
                text = re.sub(r'<style[^>]*>[\s\S]*?</style>', '', text, flags=re.IGNORECASE)
                text = re.sub(r'<[^>]+>', ' ', text)
                text = re.sub(r'\s+', ' ', text).strip()

                # Extract the title
                title_match = re.search(r'<title[^>]*>(.*?)</title>', html, re.IGNORECASE)
                title = title_match.group(1).strip() if title_match else ""

                # Extract meta description
                desc_match = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
                description = desc_match.group(1).strip() if desc_match else ""

                # Extract h1 tags
                h1_matches = re.findall(r'<h1[^>]*>(.*?)</h1>', html, re.IGNORECASE | re.DOTALL)
                h1_text = " ".join(re.sub(r'<[^>]+>', '', h).strip() for h in h1_matches[:2])

                # Build a summary of what we found
                parts = []
                if title:
                    parts.append(f"Website title: {title}")
                if description:
                    parts.append(f"Meta description: {description}")
                if h1_text:
                    parts.append(f"Headlines: {h1_text}")

                # Extract some body text (first 500 chars of visible text)
                if text and len(text) > 50:
                    # Try to find the "about" or "services" section
                    about_idx = text.lower().find("about")
                    services_idx = text.lower().find("services")
                    if about_idx >= 0 and about_idx < len(text) - 100:
                        parts.append(f"About text: {text[about_idx:about_idx+300]}")
                    elif services_idx >= 0 and services_idx < len(text) - 100:
                        parts.append(f"Services text: {text[services_idx:services_idx+300]}")
                    else:
                        parts.append(f"Page text: {text[:300]}")

                if parts:
                    return " | ".join(parts)

        return ""
    except Exception as e:
        print(f"[OUTREACH] Website scrape failed for {website_url}: {e}")
        return ""


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
    website_details: str,
) -> str:
    """Build the user-message prompt for DeepSeek."""

    company = lead.get("company_name") or lead.get("author_username") or "this business"

    # Fix the "Hi ABSENT" bug: use company name or "there" when DM name is missing
    dm_name = lead.get("dm_name") or ""
    if not dm_name or dm_name == "ABSENT":
        dm_name = ""  # Leave empty so we use "there" or company name
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

    # Determine greeting name
    greeting_name = dm_name if dm_name else "there"

    style_instruction = STYLE_PROMPTS.get(style, STYLE_PROMPTS["david_ogilvy"])

    # Build lead context — focus on what makes THIS business unique
    lead_lines = [f"LEAD BUSINESS NAME: {company}"]
    if dm_name:
        lead_lines.append(f"CONTACT PERSON: {dm_name}" + (f" ({dm_position})" if dm_position else ""))
    if website:
        lead_lines.append(f"WEBSITE: {website}")
    if phone:
        lead_lines.append(f"PHONE: {phone}")

    # Add website details (the unique part!)
    if website_details:
        lead_lines.append(f"DETAILS FROM THEIR WEBSITE: {website_details}")
    else:
        lead_lines.append("DETAILS FROM THEIR WEBSITE: (could not scrape — write a personalized message based on their business name and website URL)")

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

OUTREACH CONTEXT (CRITICAL):
These are COLD OUTREACH messages from YOU ({sender_company or 'your company'}) TO the lead business ({company}).
The goal is to start a conversation that could lead to a sale of YOUR service.
Do NOT write generic content. Do NOT mention star ratings, review counts, or platform data.
Use the DETAILS FROM THEIR WEBSITE to make each message feel like it was written specifically for {company}.

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

1. "email_subject" — a subject line for the cold email. {SUBJECT_MIN}-{SUBJECT_MAX} chars.
   Must reference something specific about {company} from their website details.
   Curiosity-driven, NOT spammy, NOT clickbait.

2. "email_message" — a cold email body. {MIN_CHARS}-{MAX_CHARS} chars.
   Start with "Hi {greeting_name},". The body MUST connect YOUR service to something
   specific you found on THEIR website. Reference a real detail about their business.{sign_off}
   Do NOT mention star ratings, review counts, or generic platform data.

3. "social_message" — a LinkedIn or Instagram DM. {MIN_CHARS}-{MAX_CHARS} chars.
   More casual tone. Must mention {company} and reference something specific from their website.
   Do NOT mention star ratings or review counts.

4. "call_script" — a phone call opening script (first 30 seconds). {MIN_CHARS}-{MAX_CHARS} chars.
   Include a greeting, mention you're calling about {company} specifically,
   reference something from their website, and ask a permission question.

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
) -> Dict[str, str]:
    """
    Generate personalized outreach messages for a single lead.
    Scrapes the lead's website for unique details to personalize the message.
    """
    if not user_service:
        raise ValueError("user_service is required")

    style = copywriting_style if copywriting_style in STYLE_PROMPTS else "david_ogilvy"

    # Scrape the lead's website for unique personalization details
    website_url = lead.get("website_url") or ""
    if website_url and website_url != "ABSENT":
        print(f"[OUTREACH] Scraping website for {lead.get('company_name', 'unknown')}: {website_url}")
        website_details = await _scrape_lead_website(website_url)
        if website_details:
            print(f"[OUTREACH] Found website details ({len(website_details)} chars)")
        else:
            print(f"[OUTREACH] No website details found")
    else:
        website_details = ""

    prompt = _build_prompt(lead, user_service, target_audience, style, sender_company, sender_name, website_details)

    # ---------- PASS 1: initial generation ----------
    print(f"[OUTREACH] Generating with model={MODEL} for lead: {lead.get('company_name', 'unknown')}")
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
                    "like star ratings or review counts. You never use placeholder text."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.7,
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

    # Fix any "Hi ABSENT" that slipped through
    for msg_attr in ['email_msg', 'social_msg', 'call_msg']:
        msg = locals()[msg_attr]
        if msg and 'ABSENT' in msg:
            msg = msg.replace('Hi ABSENT,', 'Hi there,')
            msg = msg.replace('Hi ABSENT', 'Hi there')
            msg = msg.replace('ABSENT', 'there')
            locals()[msg_attr]  # can't modify locals directly, handle below

    # Actually fix them properly
    if email_msg and 'ABSENT' in email_msg:
        email_msg = email_msg.replace('Hi ABSENT,', 'Hi there,').replace('Hi ABSENT', 'Hi there').replace('ABSENT', 'there')
    if social_msg and 'ABSENT' in social_msg:
        social_msg = social_msg.replace('Hi ABSENT,', 'Hi there,').replace('Hi ABSENT', 'Hi there').replace('ABSENT', 'there')
    if call_msg and 'ABSENT' in call_msg:
        call_msg = call_msg.replace('Hi ABSENT,', 'Hi there,').replace('Hi ABSENT', 'Hi there').replace('ABSENT', 'there')
    if email_subj and 'ABSENT' in email_subj:
        email_subj = email_subj.replace('ABSENT', lead.get("company_name") or "your business")

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
            f"It MUST be a COLD OUTREACH message referencing {company_name} specifically. "
            f"Do NOT use 'ABSENT' as a name — use 'there' or the company name. "
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
            if result and 'ABSENT' not in result:
                return result
        except Exception as e:
            print(f"[OUTREACH] Regeneration failed for {kind}: {e}")
        return current

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
