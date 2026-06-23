"""
BAD DECISION — Location Code Mapper
====================================
Converts ISO country codes (NG, US, GB) and state codes (LA, NY, TX) to
full names that Serper, Outscraper, and Yelp can actually understand.

The frontend sends country="NG" and state_region="LA". Without this mapping,
Serper searches for "LA, NG" which it can't interpret — so it defaults to
US results. With this mapping, we search for "Lagos, Nigeria" instead.
"""

# ============================================================
# COUNTRY CODE → COUNTRY NAME
# ============================================================
COUNTRY_NAMES = {
    "US": "United States", "CA": "Canada", "GB": "United Kingdom",
    "AU": "Australia", "NG": "Nigeria", "ZA": "South Africa",
    "KE": "Kenya", "GH": "Ghana", "IN": "India", "PK": "Pakistan",
    "BD": "Bangladesh", "DE": "Germany", "FR": "France", "ES": "Spain",
    "IT": "Italy", "NL": "Netherlands", "BE": "Belgium", "CH": "Switzerland",
    "AT": "Austria", "SE": "Sweden", "NO": "Norway", "DK": "Denmark",
    "FI": "Finland", "IE": "Ireland", "PT": "Portugal", "GR": "Greece",
    "PL": "Poland", "CZ": "Czech Republic", "RO": "Romania", "HU": "Hungary",
    "BR": "Brazil", "AR": "Argentina", "MX": "Mexico", "CL": "Chile",
    "CO": "Colombia", "PE": "Peru", "VE": "Venezuela", "UY": "Uruguay",
    "JP": "Japan", "KR": "South Korea", "CN": "China", "TW": "Taiwan",
    "HK": "Hong Kong", "SG": "Singapore", "MY": "Malaysia", "TH": "Thailand",
    "ID": "Indonesia", "PH": "Philippines", "VN": "Vietnam", "AE": "United Arab Emirates",
    "SA": "Saudi Arabia", "QA": "Qatar", "KW": "Kuwait", "BH": "Bahrain",
    "OM": "Oman", "JO": "Jordan", "LB": "Lebanon", "IL": "Israel",
    "TR": "Turkey", "EG": "Egypt", "MA": "Morocco", "TN": "Tunisia",
    "DZ": "Algeria", "NZ": "New Zealand", "RU": "Russia", "UA": "Ukraine",
    "BY": "Belarus", "KZ": "Kazakhstan", "UZ": "Uzbekistan",
}

# ============================================================
# STATE/REGION CODE → STATE NAME (by country)
# ============================================================
STATE_NAMES = {
    # ------ UNITED STATES ------
    "US": {
        "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
        "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
        "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
        "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
        "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
        "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
        "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
        "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
        "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
        "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
        "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
        "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
        "WI": "Wisconsin", "WY": "Wyoming", "DC": "Washington DC",
    },
    # ------ NIGERIA ------
    "NG": {
        "LA": "Lagos", "FC": "Abuja", "RI": "Rivers", "KD": "Kaduna",
        "KN": "Kano", "IB": "Ibadan", "OY": "Oyo", "OG": "Ogun",
        "ON": "Ondo", "EK": "Ekiti", "OS": "Osun", "KW": "Kwara",
        "ED": "Edo", "DT": "Delta", "BY": "Bayelsa", "AK": "Akwa Ibom",
        "CR": "Cross River", "AB": "Abia", "IM": "Imo", "AN": "Anambra",
        "EN": "Enugu", "EB": "Ebonyi", "EN2": "Enugu", "BN": "Benue",
        "PL": "Plateau", "TA": "Taraba", "AD": "Adamawa", "BO": "Borno",
        "YG": "Yobe", "GM": "Gombe", "BC": "Bauchi", "SO": "Sokoto",
        "KT": "Katsina", "JG": "Jigawa", "ZM": "Zamfara", "KB": "Kebbi",
        "NW": "Niger", "KG": "Kogi", "NA": "Nasarawa", "SD": "Sokoto",
    },
    # ------ CANADA ------
    "CA": {
        "ON": "Ontario", "QC": "Quebec", "BC": "British Columbia",
        "AB": "Alberta", "MB": "Manitoba", "SK": "Saskatchewan",
        "NS": "Nova Scotia", "NB": "New Brunswick", "NL": "Newfoundland and Labrador",
        "PE": "Prince Edward Island", "YT": "Yukon", "NT": "Northwest Territories",
        "NU": "Nunavut",
    },
    # ------ UNITED KINGDOM ------
    "GB": {
        "ENG": "England", "SCT": "Scotland", "WLS": "Wales", "NIR": "Northern Ireland",
        "LND": "London", "LDN": "London", "MAN": "Manchester", "BIR": "Birmingham", "LDS": "Leeds",
        "GLA": "Glasgow", "SHE": "Sheffield", "BRS": "Bristol", "LPL": "Liverpool",
        "EDB": "Edinburgh", "LCE": "Leicester", "CRD": "Cardiff", "BFS": "Belfast",
    },
    # ------ AUSTRALIA ------
    "AU": {
        "NSW": "New South Wales", "VIC": "Victoria", "QLD": "Queensland",
        "WA": "Western Australia", "SA": "South Australia", "TAS": "Tasmania",
        "ACT": "Australian Capital Territory", "NT": "Northern Territory",
    },
    # ------ INDIA ------
    "IN": {
        "MH": "Maharashtra", "DL": "Delhi", "KA": "Karnataka", "TN": "Tamil Nadu",
        "WB": "West Bengal", "GJ": "Gujarat", "RJ": "Rajasthan", "UP": "Uttar Pradesh",
        "TG": "Telangana", "AP": "Andhra Pradesh", "KL": "Kerala", "PB": "Punjab",
        "HR": "Haryana", "MP": "Madhya Pradesh", "BR": "Bihar", "OR": "Odisha",
        "AS": "Assam", "JH": "Jharkhand", "CT": "Chhattisgarh", "UK": "Uttarakhand",
        "HP": "Himachal Pradesh", "JK": "Jammu and Kashmir", "GA": "Goa",
    },
    # ------ SOUTH AFRICA ------
    "ZA": {
        "GT": "Gauteng", "WC": "Western Cape", "KZN": "KwaZulu-Natal",
        "EC": "Eastern Cape", "FS": "Free State", "MP": "Mpumalanga",
        "LP": "Limpopo", "NC": "Northern Cape", "NW": "North West",
    },
    # ------ UAE ------
    "AE": {
        "DX": "Dubai", "AUH": "Abu Dhabi", "SHJ": "Sharjah", "AJM": "Ajman",
        "FUJ": "Fujairah", "RAK": "Ras Al Khaimah", "UAQ": "Umm Al Quwain",
    },
}


def get_country_name(country_code: str) -> str:
    """Convert ISO country code to full country name."""
    if not country_code:
        return ""
    code = country_code.upper().strip()
    return COUNTRY_NAMES.get(code, country_code)


def get_state_name(country_code: str, state_code: str) -> str:
    """Convert state/region code to full state name."""
    if not state_code or not country_code:
        return state_code or ""
    country = country_code.upper().strip()
    state = state_code.upper().strip()
    states = STATE_NAMES.get(country, {})
    return states.get(state, state_code)


def build_location_string(country_code: str, state_code: str) -> str:
    """
    Build a human-readable location string from ISO codes.
    Example: country="NG", state="LA" → "Lagos, Nigeria"
    """
    country_name = get_country_name(country_code)
    state_name = get_state_name(country_code, state_code) if state_code else ""

    parts = [p for p in [state_name, country_name] if p]
    return ", ".join(parts) if parts else ""
