"""
BAD DECISION — Tech Stack Detector
====================================
Detects what tools and services a website uses by checking
page source for specific patterns. Free (just HTTP GET + regex).

Detects:
  - Email marketing (Klaviyo, Mailchimp)
  - Ad tracking (Meta Pixel, Google Tag Manager)
  - Review systems (Yotpo, Judge.me)
  - Subscription tools (Recharge)
  - Live chat (Tawk.to, Tidio)
  - Help desk (Gorgias, Zendesk)

Usage:
    stack = await detect_tech_stack("https://mystore.com")
    # stack = {"tools": ["Klaviyo", "Meta Pixel"], "uses_email_marketing": True, ...}
"""
import httpx
import re
from typing import Dict, Any, List

SOURCE_TIMEOUT = 10

# Tech stack detection patterns
TECH_PATTERNS = {
    "Klaviyo": {
        "patterns": [r"klaviyo", r"static\.klaviyo\.com", r"kl_id"],
        "category": "email_marketing",
    },
    "Mailchimp": {
        "patterns": [r"mailchimp", r"mc\.js", r"mcjs"],
        "category": "email_marketing",
    },
    "Meta Pixel": {
        "patterns": [r"fbq\(", r"connect\.facebook\.net", r"facebook\.com/tr", r"meta_pixel"],
        "category": "ad_tracking",
    },
    "Google Tag Manager": {
        "patterns": [r"googletagmanager\.com", r"GTM-", r"dataLayer"],
        "category": "ad_tracking",
    },
    "Google Analytics": {
        "patterns": [r"google-analytics\.com", r"gtag\(", r"UA-", r"G-"],
        "category": "analytics",
    },
    "Yotpo": {
        "patterns": [r"yotpo", r"staticw2\.yotpo\.com"],
        "category": "reviews",
    },
    "Judge.me": {
        "patterns": [r"judge\.me", r"jdgm"],
        "category": "reviews",
    },
    "Recharge": {
        "patterns": [r"recharge", r"rc\.shopify"],
        "category": "subscriptions",
    },
    "Tawk.to": {
        "patterns": [r"tawk\.to", r"tawk_"],
        "category": "live_chat",
    },
    "Tidio": {
        "patterns": [r"tidio", r"code\.tidio\.co"],
        "category": "live_chat",
    },
    "Gorgias": {
        "patterns": [r"gorgias", r"gorgias\.chat"],
        "category": "help_desk",
    },
    "Zendesk": {
        "patterns": [r"zendesk", r"zdassets"],
        "category": "help_desk",
    },
    "Hotjar": {
        "patterns": [r"hotjar", r"static\.hotjar\.com"],
        "category": "analytics",
    },
    "HubSpot": {
        "patterns": [r"hubspot", r"js\.hs-scripts\.com"],
        "category": "crm",
    },
    "Instagram": {
        "patterns": [r"instagram\.com", r"instagr\.am"],
        "category": "social",
    },
    "TikTok": {
        "patterns": [r"tiktok\.com", r"tiktok\.tv"],
        "category": "social",
    },
    "Pinterest": {
        "patterns": [r"pinterest", r"pin\.it"],
        "category": "social",
    },
    "YouTube": {
        "patterns": [r"youtube\.com", r"youtu\.be"],
        "category": "social",
    },
}


async def detect_tech_stack(website_url: str) -> Dict[str, Any]:
    """
    Detect what tools and services a website uses.

    Args:
        website_url: Full URL (e.g., "https://mystore.com")

    Returns:
        {
            "tools": ["Klaviyo", "Meta Pixel", ...],
            "uses_email_marketing": bool,
            "uses_ad_tracking": bool,
            "uses_subscriptions": bool,
            "social_media": ["Instagram", "TikTok", ...],
        }
    """
    result = {
        "tools": [],
        "uses_email_marketing": False,
        "uses_ad_tracking": False,
        "uses_subscriptions": False,
        "social_media": [],
    }

    if not website_url or website_url == "ABSENT":
        return result

    if not website_url.startswith("http"):
        website_url = "https://" + website_url

    try:
        async with httpx.AsyncClient(timeout=SOURCE_TIMEOUT, follow_redirects=True) as client:
            response = await client.get(
                website_url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; BadDecisionBot/1.0)"},
            )

            if response.status_code != 200:
                return result

            html = response.text[:50000]

            detected_categories = set()

            for tool_name, config in TECH_PATTERNS.items():
                for pattern in config["patterns"]:
                    if re.search(pattern, html, re.IGNORECASE):
                        category = config["category"]

                        if category == "social":
                            if tool_name not in result["social_media"]:
                                result["social_media"].append(tool_name)
                        else:
                            if tool_name not in result["tools"]:
                                result["tools"].append(tool_name)
                            detected_categories.add(category)

                        break  # Found this tool, no need to check more patterns

            result["uses_email_marketing"] = "email_marketing" in detected_categories
            result["uses_ad_tracking"] = "ad_tracking" in detected_categories
            result["uses_subscriptions"] = "subscriptions" in detected_categories

            return result

    except Exception:
        return result
