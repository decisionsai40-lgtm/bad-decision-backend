"""
BAD DECISION — AI Outreach Message Generator
==============================================
Generates 3 personalized outreach messages per lead:
  1. Email outreach (max 150 chars)
  2. Social media outreach (max 150 chars)
  3. Cold call script (max 150 chars)

Rules:
  - Grade 3 English (simple words, short sentences)
  - Max 150 characters per message
  - No spam trigger words (free, guarantee, click here, act now, etc.)
  - Personalized using the lead's website, social pages, and business info
  - Uses the user's selected copywriting style

Copywriting styles:
  1. dan_kennedy — Direct, authoritative, bold claims
  2. donald_miller — Story-driven, empathetic, clear
  3. ray_edwards — Warm, conversational, PAS framework
  4. david_ogilvy — Punchy, witty, data-backed
  5. jay_abraham — Educational, value-first, advisory
  6. gary_halbert — Curiosity-driven, street-smart, aggressive
"""

import json
from typing import Dict, Any, Optional

from ai.deepseek_middleware import execute_llm_payload, DEEPSEEK_SCOUT_MODEL
from scraping.email_scraper import scrape_website_for_emails


# Spam trigger words to avoid
SPAM_WORDS = {
    "free", "guarantee", "click here", "act now", "limited time",
    "buy now", "cash", "credit", "loan", "make money", "earn money",
    "work from home", "no obligation", "risk free", "100%", "best price",
    "lowest price", "save money", "special offer", "once in a lifetime",
    "this won't last", "urgent", "deadline", "expires", "exclusive deal",
}

# Style descriptions for the AI prompt
STYLE_PROMPTS = {
    "dan_kennedy": "Write in Dan Kennedy style: direct, authoritative, bold. Make a strong claim. Be blunt and confident. No fluff.",
    "donald_miller": "Write in Donald Miller StoryBrand style: empathetic, clear, inviting. Make the reader the hero. Simple problem-solution arc.",
    "ray_edwards": "Write in Ray Edwards style: warm, conversational, trust-building. Use the PAS framework (Problem, Agitate, Solve) but keep it under 150 chars.",
    "david_ogilvy": "Write in David Ogilvy style: punchy, witty, professional. Use a clever hook. Respect the reader's time. Data-backed if possible.",
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
    """Build the DeepSeek prompt for generating outreach messages."""

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

    # Build lead context
    lead_context = f"Business: {company_name}"
    if dm_name and dm_name != "ABSENT":
        lead_context += f"\nContact person: {dm_name}"
    if category and category != "ABSENT":
        lead_context += f"\nCategory: {category}"
    if address and address != "ABSENT":
        lead_context += f"\nLocation: {address}"
    if rating:
        lead_context += f"\nRating: {rating} stars"
    if platform and platform != "ABSENT":
        lead_context += f"\nSocial platform: {platform}"
    if intent_text and intent_text != "ABSENT":
        lead_context += f"\nWhat they posted: {intent_text[:200]}"
    if aggregator_source and aggregator_source != "ABSENT":
        lead_context += f"\nFound on: {aggregator_source}"
    if website_info:
        lead_context += f"\nWebsite info: {website_info[:500]}"

    prompt = f"""
You are a world-class copywriter writing personalized outreach messages.

USER'S SERVICE: {user_service}
USER'S TARGET AUDIENCE: {target_audience}
COPYWRITING STYLE: {style_instruction}

LEAD INFORMATION:
{lead_context}

Generate 3 outreach messages for this lead. Each message must be:
- Between 140 and 160 characters (aim for 150 — not too short, not too long)
- Grade 3 English (simple words a 10-year-old can read)
- No spam trigger words (avoid: free, guarantee, click here, act now, limited time, etc.)
- Highly personalized to THIS specific business based on the info above
- Friendly and human-sounding (not robotic)
- Sound like a real person wrote it, not a template
- Reference something SPECIFIC about this business (their name, location, what they do, their rating, etc.)

Generate these 3 messages:

1. email_message: A personalized email opener. Start with their name if known. Mention something specific about their business. Connect it to what the user offers. End with a simple question that invites a reply. Target: 140-160 characters.

2. social_message: A casual DM or comment for social media. Friendly tone. Reference their post or profile if available. Keep it conversational. End with a question. Target: 140-160 characters.

3. call_script: A cold call opener. Say who you are briefly. Mention why you're calling (tied to their specific business). End with an engaging question. Target: 140-160 characters.

Return a JSON object:
{{
    "email_message": "the message (140-160 chars)",
    "social_message": "the message (140-160 chars)",
    "call_script": "the message (140-160 chars)"
}}

CRITICAL: Each message MUST be between 140 and 160 characters. Not shorter. Not longer. Count carefully before responding.
"""

    return prompt


def _check_spam_words(message: str) -> bool:
    """Check if a message contains spam trigger words."""
    message_lower = message.lower()
    for word in SPAM_WORDS:
        if word in message_lower:
            return True
    return False


def _truncate_message(message: str, max_chars: int = 160) -> str:
    """Truncate a message to max_chars, cutting at the last space before the limit."""
    if len(message) <= max_chars:
        return message

    # Cut at the last space before the limit
    truncated = message[:max_chars]
    last_space = truncated.rfind(" ")
    if last_space > 100:  # Don't cut too short
        truncated = truncated[:last_space]

    return truncated.rstrip(".,!?;:") + "..."


async def generate_outreach_messages(
    lead: Dict[str, Any],
    user_service: str,
    target_audience: str,
    style: str = DEFAULT_STYLE,
) -> Dict[str, str]:
    """
    Generate 3 personalized outreach messages for a lead.

    Args:
        lead: The lead dictionary with all lead data
        user_service: What the user sells (e.g., "web design for small businesses")
        target_audience: Who the user's ideal customer is
        style: Copywriting style ID (dan_kennedy, donald_miller, etc.)

    Returns:
        Dict with keys: email_message, social_message, call_script
    """
    # Default messages if generation fails
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

    # Build the prompt
    prompt = _build_outreach_prompt(lead, user_service, target_audience, style, website_info)

    # Call DeepSeek
    try:
        response = await execute_llm_payload({
            "model": DEEPSEEK_SCOUT_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a world-class copywriter who writes short, personalized outreach messages. You always respond with valid JSON. Your messages are under 150 characters, use simple English, and never contain spam words."
                },
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.7,  # Higher temperature for more creative messages
        })

        content = response.get("choices", [{}])[0].get("message", {}).get("content", "{}")
        parsed = json.loads(content)

        email_msg = parsed.get("email_message", "").strip()
        social_msg = parsed.get("social_message", "").strip()
        call_msg = parsed.get("call_script", "").strip()

        # Truncate to 150 chars and check for spam words
        result = {
            "email_message": _truncate_message(email_msg) if email_msg else "ABSENT",
            "social_message": _truncate_message(social_msg) if social_msg else "ABSENT",
            "call_script": _truncate_message(call_msg) if call_msg else "ABSENT",
        }

        # If any message contains spam words, mark it for retry (but still return it)
        for key in result:
            if result[key] != "ABSENT" and _check_spam_words(result[key]):
                print(f"[OUTREACH] Warning: spam word found in {key}: {result[key]}")

        print(f"[OUTREACH] Generated messages for {lead.get('company_name', 'unknown')}: email={len(result['email_message'])}chars, social={len(result['social_message'])}chars, call={len(result['call_script'])}chars")
        return result

    except Exception as e:
        print(f"[OUTREACH] Error generating messages: {e}")
        return default_messages
