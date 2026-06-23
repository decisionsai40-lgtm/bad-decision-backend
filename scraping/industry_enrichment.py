"""
BAD DECISION — Industry-Specific Lead Source Enrichment
========================================================
When a user searches for "coaches", "real estate agents", "dentists", etc.,
this module generates industry-specific search queries and identifies
relevant directories/sources to search beyond generic Google Maps.

For example:
  - "coaches" → adds queries for LinkedIn, coaching directories, etc.
  - "real estate" → adds queries for Zillow, Realtor.com, MLS listings
  - "dentists" → adds queries for Healthgrades, ZocDoc, dental directories
  - "lawyers" → adds queries for Avvo, Martindale, state bar associations

This makes the engine find leads from RELEVANT sources depending on the
user's request, not just generic Google searches.
"""

import re
from typing import List, Dict, Tuple


# ============================================================
# INDUSTRY DETECTION — match user query to an industry
# ============================================================
INDUSTRY_PATTERNS: Dict[str, List[str]] = {
    "coaching": [
        "coach", "coaching", "life coach", "business coach", "career coach",
        "executive coach", "leadership coach", "wellness coach", "fitness coach",
        "relationship coach", "dating coach", "mindset coach", "success coach",
    ],
    "real_estate": [
        "real estate", "realtor", "realty", "property", "estate agent",
        "real estate agent", "real estate broker", "property manager",
        "real estate investor", "house flipper", "commercial real estate",
        "real estate agency", "real estate firm",
    ],
    "legal": [
        "lawyer", "attorney", "law firm", "legal", "solicitor", "barrister",
        "notary", "paralegal", "legal services", "law office", "law group",
        "legal counsel", "advocate",
    ],
    "medical": [
        "dentist", "dental", "doctor", "physician", "clinic", "medical",
        "healthcare", "hospital", "surgeon", "pediatrician", "dermatologist",
        "cardiologist", "orthopedic", "gynecologist", "psychiatrist",
        "chiropractor", "optometrist", "veterinary", "vet",
    ],
    "fitness": [
        "gym", "fitness", "personal trainer", "yoga", "pilates", "crossfit",
        "martial arts", "boxing", "dance studio", "zumba", "barre",
        "spin class", "bootcamp",
    ],
    "beauty": [
        "salon", "spa", "barber", "beauty", "hair", "nail", "massage",
        "esthetician", "cosmetology", "makeup", "waxing", "tanning",
        "tattoo", "piercing",
    ],
    "food": [
        "restaurant", "cafe", "coffee", "bakery", "catering", "food truck",
        "bar", "pub", "bistro", "grill", "kitchen", "diner", "pizzeria",
        "takeaway", "delivery",
    ],
    "construction": [
        "contractor", "construction", "builder", "roofing", "plumbing",
        "electrician", "hvac", "carpenter", "painter", "landscaping",
        "masonry", "concrete", "drywall", "flooring", "renovation",
        "remodeling", "handyman",
    ],
    "automotive": [
        "auto", "car", "mechanic", "automotive", "garage", "car repair",
        "auto body", "car wash", "auto detailing", "tire", "oil change",
        "car dealer", "used cars",
    ],
    "finance": [
        "accountant", "accounting", "bookkeeper", "bookkeeping", "tax",
        "financial advisor", "financial planner", "cpa", "audit",
        "payroll", "wealth management", "investment",
    ],
    "marketing": [
        "marketing", "advertising", "seo", "social media", "digital marketing",
        "ppc", "content marketing", "branding", "pr agency", "public relations",
        "web design", "web development", "graphic design",
    ],
    "education": [
        "tutor", "tutoring", "school", "academy", "training", "course",
        "institute", "learning center", "education", "teacher", "instructor",
        "montessori", "preschool", "driving school", "music school",
    ],
    "home_services": [
        "cleaning", "pest control", "pool", "lawn", "gardening",
        "moving", "storage", "security", "alarm", "smart home",
        "solar", "energy", "insulation", "windows", "doors",
    ],
    "tech": [
        "software", "saas", "startup", "app developer", "mobile app",
        "it services", "tech company", "cybersecurity", "cloud",
        "data", "ai", "machine learning", "blockchain",
    ],
    "event": [
        "event planner", "wedding", "caterer", "venue", "event space",
        "photographer", "videographer", "dj", "florist", "catering",
        "party rental", "event production",
    ],
}


# ============================================================
# INDUSTRY-SPECIFIC QUERIES — additional Serper search variations
# ============================================================
INDUSTRY_QUERIES: Dict[str, List[str]] = {
    "coaching": [
        # LinkedIn is where coaches list themselves
        "{query} LinkedIn {location}",
        "{query} directory {location}",
        "{query} certification {location}",
        "{query} services {location}",
        "{query} reviews {location}",
        "find {query} {location}",
        "best {query} {location}",
        "top {query} {location}",
        "{query} near me {location}",
        "{query} consultation {location}",
    ],
    "real_estate": [
        "{query} Zillow {location}",
        "{query} Realtor.com {location}",
        "{query} MLS listing {location}",
        "{query} agency {location}",
        "{query} broker {location}",
        "{query} reviews {location}",
        "best {query} {location}",
        "top {query} {location}",
        "{query} near me {location}",
        "{query} for sale {location}",
    ],
    "legal": [
        "{query} Avvo {location}",
        "{query} Martindale {location}",
        "{query} state bar {location}",
        "{query} law firm {location}",
        "{query} attorney {location}",
        "{query} reviews {location}",
        "best {query} {location}",
        "top {query} {location}",
        "{query} near me {location}",
        "{query} free consultation {location}",
    ],
    "medical": [
        "{query} Healthgrades {location}",
        "{query} ZocDoc {location}",
        "{query} clinic {location}",
        "{query} practice {location}",
        "{query} reviews {location}",
        "best {query} {location}",
        "top {query} {location}",
        "{query} near me {location}",
        "{query} appointment {location}",
        "{query} accepting patients {location}",
    ],
    "fitness": [
        "{query} gym {location}",
        "{query} studio {location}",
        "{query} classes {location}",
        "{query} membership {location}",
        "{query} reviews {location}",
        "best {query} {location}",
        "top {query} {location}",
        "{query} near me {location}",
        "{query} trainer {location}",
        "{query} schedule {location}",
    ],
    "beauty": [
        "{query} salon {location}",
        "{query} spa {location}",
        "{query} services {location}",
        "{query} booking {location}",
        "{query} reviews {location}",
        "best {query} {location}",
        "top {query} {location}",
        "{query} near me {location}",
        "{query} prices {location}",
        "{query} appointment {location}",
    ],
    "food": [
        "{query} restaurant {location}",
        "{query} menu {location}",
        "{query} reviews {location}",
        "{query} Yelp {location}",
        "{query} TripAdvisor {location}",
        "best {query} {location}",
        "top {query} {location}",
        "{query} near me {location}",
        "{query} delivery {location}",
        "{query} hours {location}",
    ],
    "construction": [
        "{query} contractor {location}",
        "{query} services {location}",
        "{query} company {location}",
        "{query} reviews {location}",
        "{query} licensed {location}",
        "best {query} {location}",
        "top {query} {location}",
        "{query} near me {location}",
        "{query} free estimate {location}",
        "{query} quote {location}",
    ],
    "automotive": [
        "{query} shop {location}",
        "{query} services {location}",
        "{query} reviews {location}",
        "{query} mechanic {location}",
        "{query} repair {location}",
        "best {query} {location}",
        "top {query} {location}",
        "{query} near me {location}",
        "{query} prices {location}",
        "{query} appointment {location}",
    ],
    "finance": [
        "{query} firm {location}",
        "{query} services {location}",
        "{query} CPA {location}",
        "{query} reviews {location}",
        "{query} licensed {location}",
        "best {query} {location}",
        "top {query} {location}",
        "{query} near me {location}",
        "{query} free consultation {location}",
        "{query} appointment {location}",
    ],
    "marketing": [
        "{query} agency {location}",
        "{query} firm {location}",
        "{query} services {location}",
        "{query} portfolio {location}",
        "{query} reviews {location}",
        "best {query} {location}",
        "top {query} {location}",
        "{query} near me {location}",
        "{query} case studies {location}",
        "{query} free consultation {location}",
    ],
    "education": [
        "{query} center {location}",
        "{query} school {location}",
        "{query} classes {location}",
        "{query} courses {location}",
        "{query} reviews {location}",
        "best {query} {location}",
        "top {query} {location}",
        "{query} near me {location}",
        "{query} enrollment {location}",
        "{query} schedule {location}",
    ],
    "home_services": [
        "{query} services {location}",
        "{query} company {location}",
        "{query} reviews {location}",
        "{query} licensed {location}",
        "{query} insured {location}",
        "best {query} {location}",
        "top {query} {location}",
        "{query} near me {location}",
        "{query} free quote {location}",
        "{query} appointment {location}",
    ],
    "tech": [
        "{query} company {location}",
        "{query} firm {location}",
        "{query} services {location}",
        "{query} portfolio {location}",
        "{query} reviews {location}",
        "best {query} {location}",
        "top {query} {location}",
        "{query} near me {location}",
        "{query} case studies {location}",
        "{query} consultation {location}",
    ],
    "event": [
        "{query} services {location}",
        "{query} company {location}",
        "{query} reviews {location}",
        "{query} portfolio {location}",
        "{query} booking {location}",
        "best {query} {location}",
        "top {query} {location}",
        "{query} near me {location}",
        "{query} prices {location}",
        "{query} packages {location}",
    ],
}


def detect_industry(query: str) -> Tuple[str, List[str]]:
    """
    Detect which industry the user's query belongs to.

    Args:
        query: The user's search query (e.g. "coaches", "real estate agents")

    Returns:
        (industry, matched_keywords) — e.g. ("coaching", ["coach", "coaching"])
        Returns ("generic", []) if no industry match.
    """
    query_lower = query.lower()

    # Score each industry by how many keywords match
    industry_scores: Dict[str, List[str]] = {}
    for industry, keywords in INDUSTRY_PATTERNS.items():
        matched = []
        for kw in keywords:
            if kw in query_lower:
                matched.append(kw)
        if matched:
            industry_scores[industry] = matched

    if not industry_scores:
        return ("generic", [])

    # Pick the industry with the most keyword matches
    best_industry = max(industry_scores, key=lambda k: len(industry_scores[k]))
    return (best_industry, industry_scores[best_industry])


def build_industry_queries(query: str, location: str, industry: str) -> List[str]:
    """
    Build industry-specific Serper search queries.
    Falls back to generic queries if industry is "generic".
    """
    templates = INDUSTRY_QUERIES.get(industry, [])
    if not templates:
        return []

    queries = []
    for template in templates:
        # Replace {query} and {location} placeholders
        q = template.replace("{query}", query).replace("{location}", location)
        queries.append(q)

    return queries


def get_industry_info(query: str) -> Dict:
    """
    Get full industry info for a query.
    Returns dict with:
      - industry: str (e.g. "coaching", "real_estate", "generic")
      - matched_keywords: List[str]
      - extra_queries: List[str] (industry-specific Serper queries)
    """
    industry, matched = detect_industry(query)
    extra_queries = build_industry_queries(query, "", industry) if industry != "generic" else []

    return {
        "industry": industry,
        "matched_keywords": matched,
        "extra_queries": extra_queries,
    }
