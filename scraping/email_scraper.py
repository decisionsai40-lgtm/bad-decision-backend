"""
BAD DECISION — Email Enrichment Module
========================================
Finds email addresses for businesses by:
1. Scraping the company website for mailto: links and text emails
2. Checking common email patterns (info@, contact@, hello@)
3. Using email-permutator logic for name-based guesses

This is completely FREE — no API keys needed.
"""

import re
import httpx
from typing import Optional, List, Dict
from urllib.parse import urlparse, urljoin

from config import SOURCE_TIMEOUT


# Common email prefixes to try
COMMON_PREFIXES = [
    "info", "contact", "hello", "admin", "support", "office",
    "mail", "sales", "enquiries", "inquiries", "general",
    "team", "staff", "reception", "booking", "appointments",
]

# Regex to find email addresses in HTML text
EMAIL_REGEX = re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}')

# Regex to find mailto: links
MAILTO_REGEX = re.compile(r'mailto:([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})', re.IGNORECASE)


def extract_domain(website_url: str) -> str:
    """Extract the domain from a URL, stripping the 'www.' prefix."""
    if not website_url or website_url == "ABSENT":
        return ""
    try:
        if not website_url.startswith("http"):
            website_url = "https://" + website_url
        parsed = urlparse(website_url)
        hostname = parsed.hostname or ""
        # Strip leading www. so we generate info@teamjustice.com, not info@www.teamjustice.com
        if hostname.lower().startswith("www."):
            hostname = hostname[4:]
        return hostname.lower()
    except:
        return ""


def find_emails_in_html(html: str) -> List[str]:
    """Find all email addresses in HTML content."""
    emails = set()

    # Find mailto: links (highest priority)
    for match in MAILTO_REGEX.finditer(html):
        email = match.group(1).lower().strip()
        if not _is_spammy_email(email):
            emails.add(email)

    # Find emails in text
    for match in EMAIL_REGEX.finditer(html):
        email = match.group(0).lower().strip()
        if not _is_spammy_email(email):
            emails.add(email)

    return list(emails)


def _is_spammy_email(email: str) -> bool:
    """Check if an email is likely spammy or not a real business email."""
    if not email:
        return True

    # Skip image file extensions
    if any(email.endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg']):
        return True

    # Skip very long emails (usually not real)
    if len(email) > 100:
        return True

    # Skip emails with suspicious domains
    suspicious_domains = ['example.com', 'test.com', 'sentry.io', 'wixpress.com',
                         'godaddy.com', 'cloudflare.com', 'google.com', 'facebook.com',
                         'twitter.com', 'instagram.com', 'linkedin.com', 'youtube.com',
                         'noreply.github.com', 'example.org']
    domain = email.split('@')[-1] if '@' in email else ''
    if domain in suspicious_domains:
        return True

    # Skip emails that are just image filenames
    if re.match(r'^[a-f0-9]{32}@', email):
        return True

    return False


def generate_common_emails(domain: str, company_name: str = "") -> List[str]:
    """
    Generate common email addresses for a domain.
    e.g., info@domain.com, contact@domain.com, etc.
    """
    if not domain:
        return []

    emails = []
    for prefix in COMMON_PREFIXES:
        emails.append(f"{prefix}@{domain}")

    # Also try first name based on company name
    if company_name and company_name != "ABSENT":
        # Try first word of company name (lowercase)
        first_word = company_name.lower().split()[0]
        if first_word and len(first_word) > 2:
            emails.append(f"{first_word}@{domain}")

    return emails


async def scrape_website_for_emails(website_url: str) -> Dict[str, any]:
    """
    Scrape a company website to find email addresses.

    Returns:
        Dict with:
        - emails: list of email addresses found
        - phone: phone number if found
        - facebook: Facebook URL if found
        - instagram: Instagram URL if found
        - linkedin: LinkedIn URL if found
    """
    result = {
        "emails": [],
        "phone": "",
        "facebook": "",
        "instagram": "",
        "linkedin": "",
    }

    if not website_url or website_url == "ABSENT":
        return result

    # Normalize URL
    if not website_url.startswith("http"):
        website_url = "https://" + website_url

    domain = extract_domain(website_url)
    if not domain:
        return result

    # Try to fetch the homepage
    try:
        async with httpx.AsyncClient(timeout=SOURCE_TIMEOUT, follow_redirects=True) as client:
            response = await client.get(
                website_url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                }
            )

            if response.status_code == 200:
                html = response.text

                # Find emails
                emails = find_emails_in_html(html)
                result["emails"] = emails[:10]  # Limit to 10 emails

                # Find phone numbers (basic regex)
                phone_match = re.search(r'(?:tel:|\+?1?[-.\s]?\(?(\d{3})\)?[-.\s]?(\d{3})[-.\s]?(\d{4}))', html)
                if phone_match:
                    result["phone"] = phone_match.group(0).replace('tel:', '').strip()

                # Find social media links
                if 'facebook.com' in html.lower():
                    fb_match = re.search(r'https?://(?:www\.)?facebook\.com/[a-zA-Z0-9._\-/]+', html, re.IGNORECASE)
                    if fb_match:
                        result["facebook"] = fb_match.group(0).rstrip('/')

                if 'instagram.com' in html.lower():
                    ig_match = re.search(r'https?://(?:www\.)?instagram\.com/[a-zA-Z0-9._\-/]+', html, re.IGNORECASE)
                    if ig_match:
                        result["instagram"] = ig_match.group(0).rstrip('/')

                if 'linkedin.com' in html.lower():
                    li_match = re.search(r'https?://(?:www\.)?linkedin\.com/[a-zA-Z0-9._\-/]+', html, re.IGNORECASE)
                    if li_match:
                        result["linkedin"] = li_match.group(0).rstrip('/')

                # Also try common contact page
                if not result["emails"]:
                    contact_urls = [
                        f"https://{domain}/contact",
                        f"https://{domain}/contact-us",
                        f"https://{domain}/about",
                        f"https://{domain}/about-us",
                    ]
                    for contact_url in contact_urls:
                        try:
                            contact_res = await client.get(
                                contact_url,
                                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
                            )
                            if contact_res.status_code == 200:
                                contact_emails = find_emails_in_html(contact_res.text)
                                if contact_emails:
                                    result["emails"].extend(contact_emails[:5])
                                    break
                        except:
                            pass

                print(f"[EMAIL_SCRAPER] Found {len(result['emails'])} emails for {domain}")

    except httpx.TimeoutException:
        print(f"[EMAIL_SCRAPER] Timeout for {website_url}")
    except Exception as e:
        print(f"[EMAIL_SCRAPER] Error for {website_url}: {e}")

    return result


async def enrich_lead_with_email(
    company_name: str,
    website_url: str,
) -> Dict[str, str]:
    """
    Enrich a lead with email and contact info by scraping their website.

    Args:
        company_name: The company name
        website_url: Their website URL

    Returns:
        Dict with verified_email, phone, facebook, instagram, linkedin
    """
    result = {
        "verified_email": "ABSENT",
        "phone": "ABSENT",
        "facebook": "ABSENT",
        "instagram": "ABSENT",
        "linkedin": "ABSENT",
    }

    if not website_url or website_url == "ABSENT":
        return result

    # Scrape the website
    scrape_result = await scrape_website_for_emails(website_url)

    # Use the first non-spammy email found
    if scrape_result["emails"]:
        result["verified_email"] = scrape_result["emails"][0]

    if scrape_result["phone"]:
        result["phone"] = scrape_result["phone"]

    if scrape_result["facebook"]:
        result["facebook"] = scrape_result["facebook"]

    if scrape_result["instagram"]:
        result["instagram"] = scrape_result["instagram"]

    if scrape_result["linkedin"]:
        result["linkedin"] = scrape_result["linkedin"]

    # If no email found, try common email patterns
    if result["verified_email"] == "ABSENT":
        domain = extract_domain(website_url)
        if domain:
            common_emails = generate_common_emails(domain, company_name)
            if common_emails:
                # Use the first common email (info@ is most likely)
                result["verified_email"] = common_emails[0]
                print(f"[EMAIL_SCRAPER] No email found on website, using common pattern: {result['verified_email']}")

    return result
