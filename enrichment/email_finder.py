"""
BAD DECISION AI — Email Finder Pipeline
========================================
Multi-strategy email discovery for businesses.
All strategies are FREE (no paid APIs required).
"""

import re
import dns.resolver
import smtplib
import random
import string
from typing import Optional, List, Dict, Any
from scraping.stealth_fetcher import (
    stealth_fetch,
    extract_text_from_html,
    extract_emails_from_html,
    extract_phones_from_html,
    is_js_shell,
)
from api_clients.hunter_client import hunter_domain_search
from api_clients.firecrawl_client import firecrawl_scrape


# Generic email prefixes commonly used by small businesses
GENERIC_EMAIL_PREFIXES = [
    "info", "contact", "hello", "office", "enquiries",
    "bookings", "support", "admin", "mail", "reception",
    "hello", "hi", "team", "sales", "inquiry",
]


def generate_email_patterns(first_name: str, last_name: str, domain: str) -> List[Dict[str, Any]]:
    """Generate common email pattern candidates for a person at a domain."""
    f = first_name.lower().strip() if first_name else ""
    l = last_name.lower().strip() if last_name else ""
    fi = f[0] if f else ""
    li = l[0] if l else ""

    if not f and not l:
        return []

    patterns = [
        (f"{f}.{l}@{domain}", 0.30),
        (f"{fi}{l}@{domain}", 0.20),
        (f"{f}@{domain}", 0.15),
        (f"{f}{l}@{domain}", 0.10),
        (f"{fi}.{l}@{domain}", 0.08),
        (f"{f}{li}@{domain}", 0.04),
        (f"{l}.{f}@{domain}", 0.03),
        (f"{l}{fi}@{domain}", 0.03),
        (f"{f}_{l}@{domain}", 0.02),
        (f"{f}-{l}@{domain}", 0.02),
    ]

    return [{"email": e, "confidence": c} for e, c in patterns]


async def find_emails_for_domain(domain: str, first_name: str = "", last_name: str = "") -> Dict[str, Any]:
    """
    Multi-strategy email finder for a business domain.
    Returns the best email found + the email pattern.
    """
    # Clean domain
    domain = domain.lower().strip()
    domain = re.sub(r'^https?://', '', domain)
    domain = re.sub(r'/.*$', '', domain)
    domain = domain.strip('/')

    result = {
        "verified_email": "ABSENT",
        "email_source": "ABSENT",
        "email_pattern": "ABSENT",
        "all_emails_found": [],
    }

    # STRATEGY 1: Hunter.io domain search (25/month free)
    hunter_data = await hunter_domain_search(domain)
    if hunter_data and hunter_data.get("emails"):
        best_email = None
        best_confidence = 0
        for email_data in hunter_data["emails"]:
            email = email_data.get("email", "")
            confidence = email_data.get("confidence", 0)
            result["all_emails_found"].append(email)
            if confidence > best_confidence:
                best_email = email
                best_confidence = confidence

        if best_email:
            result["verified_email"] = best_email
            result["email_source"] = "hunter"
            result["email_pattern"] = hunter_data.get("pattern", "ABSENT")
            return result

    # STRATEGY 2: Scrape website for emails (Scrapling — free)
    for page in ["", "/contact", "/about"]:
        url = f"https://{domain}{page}"
        site_result = await stealth_fetch(url)
        if site_result and site_result.get("html"):
            html = site_result["html"]

            # Check for JS shell
            if is_js_shell(html):
                continue

            # Direct regex email extraction from HTML
            emails = extract_emails_from_html(html)
            domain_emails = [e for e in emails if domain in e]

            if domain_emails:
                result["verified_email"] = domain_emails[0]
                result["email_source"] = "website_scrape"
                result["all_emails_found"].extend(domain_emails)
                return result

    # STRATEGY 3: Firecrawl for JS-rendered sites (1K free credits/month)
    fc_markdown = await firecrawl_scrape(f"https://{domain}/contact")
    if fc_markdown:
        emails = re.findall(r'[a-zA-Z0-9._%+\-]+@' + re.escape(domain), fc_markdown)
        if emails:
            result["verified_email"] = emails[0]
            result["email_source"] = "firecrawl"
            result["all_emails_found"].extend(emails)
            return result

    # STRATEGY 4: Email pattern prediction + SMTP verification
    if first_name and last_name:
        patterns = generate_email_patterns(first_name, last_name, domain)

        # Check MX records first
        if not _domain_has_mx(domain):
            return result

        # Try top 3 patterns with SMTP
        for pattern in patterns[:3]:
            email = pattern["email"]
            valid = await _smtp_check(email)
            if valid:
                result["verified_email"] = email
                result["email_source"] = "pattern_smtp"
                result["email_pattern"] = _infer_pattern(email, first_name, last_name)
                return result

    # STRATEGY 5: Generic business emails (info@, contact@)
    if _domain_has_mx(domain):
        for prefix in GENERIC_EMAIL_PREFIXES[:3]:
            email = f"{prefix}@{domain}"
            valid = await _smtp_check(email)
            if valid:
                result["verified_email"] = email
                result["email_source"] = "generic_smtp"
                return result

    return result


async def find_email_for_web_absent(
    company_name: str,
    aggregator_url: str = "",
    location: str = "",
) -> Dict[str, Any]:
    """
    Find emails for businesses that don't have their own website.
    Uses different strategies than domain-based search.
    """
    result = {
        "verified_email": "ABSENT",
        "email_source": "ABSENT",
    }

    # STRATEGY 1: Scrape aggregator profile page for email
    if aggregator_url and aggregator_url != "ABSENT":
        # Try Scrapling first
        site_result = await stealth_fetch(aggregator_url)
        if site_result and site_result.get("html"):
            html = site_result["html"]
            if not is_js_shell(html):
                emails = extract_emails_from_html(html)
                # Filter for business emails (not platform emails)
                business_emails = [e for e in emails if not any(
                    skip in e for skip in [
                        'yelp.com', 'houzz.com', 'facebook.com', 'google.com',
                        'instagram.com', 'twitter.com',
                    ]
                )]
                if business_emails:
                    result["verified_email"] = business_emails[0]
                    result["email_source"] = "aggregator_profile"
                    return result

        # Try Firecrawl for JS-heavy aggregator pages
        fc_content = await firecrawl_scrape(aggregator_url)
        if fc_content:
            emails = re.findall(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}', fc_content)
            business_emails = [e for e in emails if not any(
                skip in e for skip in [
                    'yelp.com', 'houzz.com', 'facebook.com', 'google.com',
                    'instagram.com', 'twitter.com',
                ]
            )]
            if business_emails:
                result["verified_email"] = business_emails[0]
                result["email_source"] = "aggregator_firecrawl"
                return result

    # STRATEGY 2: Web search for business email
    try:
        from duckduckgo_search import DDGS
        queries = [
            f'"{company_name}" {location} email',
            f'"{company_name}" {location} contact email',
        ]
        for query in queries:
            try:
                with DDGS() as ddgs:
                    search_results = list(ddgs.text(query, max_results=5))
                    for r in search_results:
                        snippet = r.get("body", "") + r.get("title", "")
                        emails = re.findall(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}', snippet)
                        business_emails = [e for e in emails if not any(
                            skip in e for skip in [
                                'yelp.com', 'facebook.com', 'google.com', 'example.com',
                            ]
                        )]
                        if business_emails:
                            result["verified_email"] = business_emails[0]
                            result["email_source"] = "web_search"
                            return result
            except Exception:
                continue
    except ImportError:
        pass

    return result


def _domain_has_mx(domain: str) -> bool:
    """Check if a domain has MX records (accepts email)."""
    try:
        records = dns.resolver.resolve(domain, 'MX')
        return len(records) > 0
    except:
        return False


async def _smtp_check(email: str) -> bool:
    """Quick SMTP check to see if an email might exist."""
    try:
        domain = email.split('@')[1]
        mx_records = dns.resolver.resolve(domain, 'MX')
        if not mx_records:
            return False

        mx_host = str(sorted(mx_records, key=lambda r: r.preference)[0].exchange).rstrip('.')

        server = smtplib.SMTP(timeout=8)
        server.connect(mx_host, 25)
        server.ehlo("verify.baddecision.ai")
        server.mail("verify@baddecision.ai")
        code, _ = server.rcpt(email)
        server.quit()

        return code == 250
    except:
        return False


def _infer_pattern(email: str, first_name: str, last_name: str) -> str:
    """Infer the email pattern from a verified email."""
    local_part = email.split('@')[0]
    f = first_name.lower()
    l = last_name.lower()

    if local_part == f"{f}.{l}":
        return "{first}.{last}"
    elif local_part == f"{f[0]}{l}":
        return "{fi}{last}"
    elif local_part == f:
        return "{first}"
    elif local_part == f"{f}{l}":
        return "{first}{last}"
    elif local_part == f"{f[0]}.{l}":
        return "{fi}.{last}"
    else:
        return "unknown"
