"""
BAD DECISION AI — Gate 2: Footprint Check
==========================================
Verifies a lead has at least ONE viable contact method.
Starter tier and above.
"""

import re
from typing import Dict, Any


def check_footprint(lead: Dict[str, Any]) -> bool:
    """Check if a lead has at least ONE viable contact method."""
    contact_found = False

    email = lead.get("verified_email", "ABSENT")
    if email != "ABSENT" and email and _looks_like_email(email):
        contact_found = True

    phone = lead.get("phone", "ABSENT")
    if phone != "ABSENT" and phone and _looks_like_phone(phone):
        contact_found = True

    linkedin = lead.get("linkedin", "ABSENT")
    if linkedin != "ABSENT" and linkedin and "linkedin" in linkedin.lower():
        contact_found = True

    instagram = lead.get("instagram", "ABSENT")
    if instagram != "ABSENT" and instagram and "instagram" in instagram.lower():
        contact_found = True

    dm_name = lead.get("dm_name", "ABSENT")
    if dm_name != "ABSENT" and dm_name and len(dm_name) > 2:
        contact_found = True

    if not contact_found:
        print(f"[GATE2-FOOTPRINT] {lead.get('company_name', 'Unknown')} — No contact method")

    return contact_found


def _looks_like_email(text: str) -> bool:
    pattern = r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, text.strip()))


def _looks_like_phone(text: str) -> bool:
    digits = re.sub(r'[^0-9]', '', text)
    return len(digits) >= 7
