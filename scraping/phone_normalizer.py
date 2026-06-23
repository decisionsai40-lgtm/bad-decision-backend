"""
BAD DECISION — Phone Number Normalizer
=======================================
Normalizes phone numbers to E.164 format with country code.
CheckNumber.ai (and WhatsApp/Telegram) require the full international
format (+234..., +1..., +44...) to work correctly.

If a phone number is missing the country code, we add it based on the
country the user searched in. For example, if the user searched in
Nigeria (NG) and the phone is "0803 123 4567", we convert it to
"+2348031234567".
"""

import re
import phonenumbers
from typing import Optional
from scraping.location_mapper import get_country_name


# ISO country code → ISO 3166-1 alpha-2 (for phonenumbers library)
# phonenumbers uses 2-letter ISO codes, same as our country codes.
# We just need to pass the country code to phonenumbers.parse().


def normalize_phone(phone: str, country_code: str = "") -> str:
    """
    Normalize a phone number to E.164 format with country code.

    Args:
        phone: Raw phone number (e.g., "0803 123 4567", "(212) 555-1234")
        country_code: ISO country code of the search location (e.g., "NG", "US")

    Returns:
        Normalized phone in E.164 format (e.g., "+2348031234567")
        Returns the original if normalization fails.
    """
    if not phone or phone == "ABSENT":
        return phone

    # Quick clean: remove everything except digits and +
    cleaned = re.sub(r'[^\d+]', '', phone)

    if not cleaned:
        return phone

    # If it already starts with +, it likely has a country code
    if cleaned.startswith('+'):
        return cleaned

    # If it starts with 00, that's an international prefix (00 → +)
    if cleaned.startswith('00'):
        return '+' + cleaned[2:]

    # Try to parse with the country code
    try:
        # phonenumbers expects a region code (2-letter ISO)
        region = country_code.upper() if country_code else None
        parsed = phonenumbers.parse(cleaned, region)

        if phonenumbers.is_valid_number(parsed):
            # Format as E.164 (+2348031234567)
            return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    except Exception:
        pass

    # Fallback: if we know the country code, prepend it
    if country_code and not cleaned.startswith('+'):
        cc = _get_calling_code(country_code)
        if cc:
            # Remove leading 0 if present (common in local numbers)
            local = cleaned.lstrip('0')
            # Avoid double-prefix if the number already starts with the calling code
            if local.startswith(cc):
                return '+' + local
            return '+' + cc + local

    # Last resort: return with + prefix if it looks like a full number
    if len(cleaned) >= 10:
        return '+' + cleaned

    return phone


def _get_calling_code(country_code: str) -> str:
    """Get the international calling code for a country (e.g., NG → 234)."""
    try:
        import phonenumbers
        # phonenumbers.country_code_for_region returns the calling code as int
        code = phonenumbers.country_code_for_region(country_code.upper())
        if code and code > 0:
            return str(code)
    except Exception:
        pass

    # Fallback map for common countries
    FALLBACK = {
        "US": "1", "CA": "1", "GB": "44", "AU": "61", "NG": "234",
        "ZA": "27", "KE": "254", "GH": "233", "IN": "91", "PK": "92",
        "BD": "880", "DE": "49", "FR": "33", "ES": "34", "IT": "39",
        "NL": "31", "AE": "971", "SA": "966", "JP": "81", "KR": "82",
        "CN": "86", "BR": "55", "MX": "52", "RU": "7", "TR": "90",
        "EG": "20", "SG": "65", "MY": "60", "PH": "63", "TH": "66",
        "ID": "62", "VN": "84", "NZ": "64", "IE": "353", "SE": "46",
        "NO": "47", "DK": "45", "FI": "358", "PL": "48", "PT": "351",
        "GR": "30", "CZ": "420", "AR": "54", "CL": "56", "CO": "57",
        "PE": "51", "CH": "41", "AT": "43", "BE": "32", "UA": "380",
        "RO": "40", "HU": "36", "IL": "972", "QA": "974", "KW": "965",
        "BH": "973", "OM": "968", "JO": "962", "LB": "961", "MA": "212",
        "TN": "216", "DZ": "213",
    }
    return FALLBACK.get(country_code.upper(), "")
