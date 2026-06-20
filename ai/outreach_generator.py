"""
BAD DECISION — AI Outreach Message Generator
==============================================
Generates 3 personalized outreach messages per lead:
  1. Email outreach (500-530 chars)
  2. Social media outreach (500-530 chars)
  3. Cold call script (500-530 chars)

Rules:
  - Grade 3 English (simple words, short sentences)
  - 500-530 characters per message
  - No spam trigger words
  - NO placeholders — use actual lead data (name, company, etc.)
  - Personalized using the lead's website, social pages, and business info
  - Uses the user's selected copywriting style
"""

import json
import re
from typing import Dict, Any

from ai.deepseek_middleware import execute_llm_payload, DEEPSEEK_SCOUT_MODEL
from scraping.email_scraper import scrape_website_for_emails


SPAM_WORDS = {
    "free", "guarantee", "click here", "act now", "limited time",
    "buy now", "cash", "credit", "loan", "make money", "earn money",
    "work from home", "no obligation", "risk free", "100%", "best price",
    "lowest price", "save money", "special offer", "once in a lifetime",
    "this won't last", "urgent", "deadline", "expires", "exclusive deal",
}

STYLE_PROMPTS = {
    "dan_kennedy": "Write in Dan Kennedy style: direct, authoritative, bold. Make a strong claim. Be blunt and confident. No fluff.",
    "donald_miller": "Write in Donald Miller StoryBrand style: empathetic, clear, inviting. Make the reader the hero. Simple problem-solution arc.",
    "ray_edwards": "Write in Ray Edwards style: warm, conversational, trust-building. Use the PAS framework (Problem, Agitate, Solve).",
    "david_ogilvy": "Write in David Ogilvy style: punchy, witty, professional. Use a clever hook. Respect the reader's time.",
    "jay_abraham": "Write in Jay Abraham style: advisory, value-first, strategic. Educate the prospect. Show you understand their business.",
    "gary_halbert": "Write in Gary Halbert style: curiosity-driven, street-smart, conversational. Use a hook that makes them want to know more.",
}

DEFAULT_STYLE = "david_ogilvy"


def _build_outreach_prompt(
    lead: Dict[str, Any],
    user_service: str,
    target_audience: str,
    style: str,
    website_info: str,
) -> str:
    style_instruction = STYLE_PROMPTS.get(style, STYLE_PROMPTS[DEFAULT_STYLE])

    company_name = lead.get("company_name", "the business")
    dm_name = lead.get("dm_name", "ABSENT")
    address = lead.get("address", "ABSENT")
    phone = lead.get("phone", "ABSENT")
    rating = lead.get("rating")
    category = lead.get("category", "ABSENT")
    intent_text = lead.get("intent_text", "ABSENT")
    platform = lead.get("platform", "ABSENT")
    aggregator_source = lead.get("aggregator_source", "ABSENT")
    website_url = lead.get("website_url", "ABSENT")
    verified_email = lead.get("verified_email", "ABSENT")

    # Build lead context with ACTUAL values (no placeholders)
    lead_context = f"Business name: {company_name}"
    if dm_name and dm_name != "ABSENT":
        lead_context += f"\nContact person name: {dm_name}"
    if category and category != "ABSENT" and category:
        lead_context += f"\nBusiness type: {category}"
    if address and address != "ABSENT" and address:
        lead_context += f"\nLocation: {address}"
    if rating:
        lead_context += f"\nRating: {rating} stars"
    if phone and phone != "ABSENT" and phone:
        lead_context += f"\nPhone: {phone}"
    if website_url and website_url != "ABSENT" and website_url:
        lead_context += f"\nWebsite: {website_url}"
    if verified_email and verified_email != "ABSENT" and verified_email:
        lead_context += f"\nEmail: {verified_email}"
    if platform and platform != "ABSENT" and platform:
        lead_context += f"\nSocial platform: {platform}"
    if intent_text and intent_text != "ABSENT" and intent_text:
        lead_context += f"\nWhat they posted: {intent_text[:300]}"
    if aggregator_source and aggregator_source != "ABSENT" and aggregator_source:
        lead_context += f"\nFound on: {aggregator_source}"
    if website_info:
        lead_context += f"\nWebsite details: {website_info[:500]}"

    prompt = f"""You are a world-class copywriter writing personalized outreach messages.

USER'S SERVICE: {user_service}
USER'S TARGET AUDIENCE: {target_audience}
COPYWRITING STYLE: {style_instruction}

LEAD INFORMATION:
{lead_context}

Generate 3 outreach messages for this lead.

CRITICAL RULES:
1. Each message MUST be between 500 and 530 characters. Count every character carefully.
2. Use ACTUAL data from the lead above. Use their real business name, real contact name, real location, real website.
3. NEVER use placeholders like [name], [company], [your name], etc. Always use the actual values.
4. Grade 3 English (simple words a 10-year-old can read).
5. No spam trigger words (avoid: free, guarantee, click here, act now, limited time, etc.)
6. Highly personalized to THIS specific business.
7. Sound like a real person wrote it, not a template.
8. Reference something SPECIFIC about this business (their name, location, what they do, their rating, etc.)
9. Connect what the user offers to what this specific business needs.

Generate these 3 messages:

1. email_message: A personalized email. Start with the contact person's name if available (use their ACTUAL name, not a placeholder). If no name is available, use the business name. Mention something specific about their business. Explain briefly what the user offers and why it matters to them. End with a clear question. Target: 500-530 characters.

2. social_message: A casual DM for social media. Friendly tone. If they posted something, reference their actual post. Keep it conversational but professional. End with a question. Target: 500-530 characters.

3. call_script: A cold call script. Say who you are (use the user's service). Mention the business by name. Say why you're calling (tied to their specific business). Have a clear hook. End with an engaging question to start the conversation. Target: 500-530 characters.

Return a JSON object:
{{
    "email_message": "the actual message (500-530 chars)",
    "social_message": "the actual message (500-530 chars)",
    "call_script": "the actual message (500-530 chars)"
}}

REMEMBER: Use REAL names and REAL business details. NO placeholders. NO [brackets]. Each message 500-530 characters.
"""

    return prompt


def _check_spam_words(message: str) -> bool:
    message_lower = message.lower()
    for word in SPAM_WORDS:
        if word in message_lower:
            return True
    return False


def _has_placeholders(message: str) -> bool:
    """Check if message contains placeholders like [name], {company}, etc."""
    if not message or message == "ABSENT":
        return False
    # Check for common placeholder patterns
    placeholder_patterns = [
        r'\[.*?\]',  # [name], [company]
        r'\{.*?\}',  # {name}, {company}
        r'<.*?>',    # <name>, <company>
        r'\bYOUR_NAME\b', r'\bYOUR_COMPANY\b', r'\bTHEIR_NAME\b', r'\bLEAD_NAME\b',
        r'\bCOMPANY_NAME\b', r'\bBUSINESS_NAME\b',
    ]
    for pattern in placeholder_patterns:
        if re.search(pattern, message, re.IGNORECASE):
            return True
    return False


def _fix_placeholders(message: str, lead: Dict[str, Any]) -> str:
    """Replace any placeholders with actual lead data."""
    if not message or message == "ABSENT":
        return message

    company_name = lead.get("company_name", "your business")
    dm_name = lead.get("dm_name", "")
    name_to_use = dm_name if dm_name and dm_name != "ABSENT" else company_name

    replacements = {
        "[name]": name_to_use,
        "[Name]": name_to_use,
        "[NAME]": name_to_use,
        "[company]": company_name,
        "[Company]": company_name,
        "[COMPANY]": company_name,
        "[business]": company_name,
        "[Business]": company_name,
        "[BUSINESS]": company_name,
        "[lead_name]": name_to_use,
        "[Lead_Name]": name_to_use,
        "[company_name]": company_name,
        "[Company_Name]": company_name,
        "[business_name]": company_name,
        "[Business_Name]": company_name,
        "{name}": name_to_use,
        "{company}": company_name,
        "{business}": company_name,
        "{company_name}": company_name,
        "{business_name}": company_name,
        "<name>": name_to_use,
        "<company>": company_name,
        "<business>": company_name,
        "YOUR_NAME": name_to_use,
        "YOUR_COMPANY": company_name,
        "THEIR_NAME": name_to_use,
        "LEAD_NAME": name_to_use,
        "COMPANY_NAME": company_name,
        "BUSINESS_NAME": company_name,
    }

    for placeholder, value in replacements.items():
        message = message.replace(placeholder, value)

    # Also fix any remaining bracketed placeholders
    message = re.sub(r'\[([^\]]+)\]', company_name, message)
    message = re.sub(r'\{([^\}]+)\}', company_name, message)
    message = re.sub(r'<([^>]+)>', company_name, message)

    return message


def _truncate_message(message: str, max_chars: int = 530) -> str:
    if len(message) <= max_chars:
        return message
    truncated = message[:max_chars]
    last_space = truncated.rfind(" ")
    if last_space > 400:
        truncated = truncated[:last_space]
    return truncated.rstrip(".,!?;:") + "..."


async def generate_outreach_messages(
    lead: Dict[str, Any],
    user_service: str,
    target_audience: str,
    style: str = DEFAULT_STYLE,
) -> Dict[str, str]:
    default_messages = {
        "email_message": "ABSENT",
        "social_message": "ABSENT",
        "call_script": "ABSENT",
    }

    if not user_service or user_service.strip() == "":
        return default_messages

    # Scrape the lead's website for personalization data
    website_info = ""
    website_url = lead.get("website_url", "ABSENT")
    if website_url and website_url != "ABSENT":
        try:
            scrape_result = await scrape_website_for_emails(website_url)
            if scrape_result.get("emails"):
                website_info += f"Found emails on site: {', '.join(scrape_result['emails'][:3])}. "
            if scrape_result.get("phone"):
                website_info += f"Phone on site: {scrape_result['phone']}. "
            if scrape_result.get("facebook"):
                website_info += f"Has Facebook page. "
            if scrape_result.get("instagram"):
                website_info += f"Has Instagram. "
            if scrape_result.get("linkedin"):
                website_info += f"Has LinkedIn. "
        except:
            pass

    prompt = _build_outreach_prompt(lead, user_service, target_audience, style, website_info)

    try:
        response = await execute_llm_payload({
            "model": DEEPSEEK_SCOUT_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a world-class copywriter who writes personalized outreach messages. You always respond with valid JSON. Your messages are 500-530 characters, use simple English, and never contain spam words or placeholders."
                },
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.7,
        })

        content = response.get("choices", [{}])[0].get("message", {}).get("content", "{}")
        parsed = json.loads(content)

        email_msg = parsed.get("email_message", "").strip()
        social_msg = parsed.get("social_message", "").strip()
        call_msg = parsed.get("call_script", "").strip()

        # Fix any placeholders
        email_msg = _fix_placeholders(email_msg, lead)
        social_msg = _fix_placeholders(social_msg, lead)
        call_msg = _fix_placeholders(call_msg, lead)

        # Truncate to 530 chars max
        result = {
            "email_message": _truncate_message(email_msg) if email_msg else "ABSENT",
            "social_message": _truncate_message(social_msg) if social_msg else "ABSENT",
            "call_script": _truncate_message(call_msg) if call_msg else "ABSENT",
        }

        # Check for spam words and placeholders
        for key in result:
            if result[key] != "ABSENT":
                if _check_spam_words(result[key]):
                    print(f"[OUTREACH] Warning: spam word found in {key}")
                if _has_placeholders(result[key]):
                    print(f"[OUTREACH] Warning: placeholder found in {key}: {result[key][:100]}")

        print(f"[OUTREACH] Generated for {lead.get('company_name', 'unknown')}: email={len(result['email_message'])}chars, social={len(result['social_message'])}chars, call={len(result['call_script'])}chars")
        return result

    except Exception as e:
        print(f"[OUTREACH] Error generating messages: {e}")
        return default_messages
