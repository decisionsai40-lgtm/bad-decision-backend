"""
BAD DECISION AI — Gate 2: Footprint Check
==========================================
This is the MEDIUM-speed check. We verify that the lead
has at least ONE way to contact them — a phone number,
an email, a LinkedIn profile, or an Instagram page.

If a lead has ZERO contact methods, there's no point
keeping it — nobody can reach them.

Speed: Fast (regex check, < 1 second)
Who gets it: Starter, Growth, Pro (NOT Free tier)
"""

import re
from typing import Dict, Any


def check_footprint(lead: Dict[str, Any]) -> bool:
    """
    Check if a lead has at least ONE viable contact method.

    Think of it like: "Can we actually reach this person?"
    If they have no email, no phone, no LinkedIn, no Instagram...
    then this lead is useless.

    Args:
        lead: The lead dictionary with all its data

    Returns:
        True = at least one contact method exists, False = dead lead
    """

    contact_found = False

    # Check for a real email (not ABSENT)
    email = lead.get("verified_email", "ABSENT")
    if email != "ABSENT" and email and _looks_like_email(email):
        contact_found = True

    # Check for a real phone number (not ABSENT)
    phone = lead.get("phone", "ABSENT")
    if phone != "ABSENT" and phone and _looks_like_phone(phone):
        contact_found = True

    # Check for a real LinkedIn URL (not ABSENT)
    linkedin = lead.get("linkedin", "ABSENT")
    if linkedin != "ABSENT" and linkedin and "linkedin" in linkedin.lower():
        contact_found = True

    # Check for a real Instagram URL (not ABSENT)
    instagram = lead.get("instagram", "ABSENT")
    if instagram != "ABSENT" and instagram and "instagram" in instagram.lower():
        contact_found = True

    # Check for a decision maker name (at least we know who to ask for)
    dm_name = lead.get("dm_name", "ABSENT")
    if dm_name != "ABSENT" and dm_name and len(dm_name) > 2:
        contact_found = True

    if not contact_found:
        print(f"[GATE2-FOOTPRINT] {lead.get('company_name', 'Unknown')} — No contact method found")

    return contact_found


def _looks_like_email(text: str) -> bool:
    """Check if a string looks like an email address."""
    pattern = r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, text.strip()))


def _looks_like_phone(text: str) -> bool:
    """Check if a string looks like a phone number (at least 7 digits)."""
    digits = re.sub(r'[^0-9]', '', text)
    return len(digits) >= 7
