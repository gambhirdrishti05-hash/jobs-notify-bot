"""
Job Alert Bot — checks company career pages for new postings.

Supported platforms (reliable, uses structured API):
  - Greenhouse  (boards-api.greenhouse.io)
  - Lever       (api.lever.co)
  - Workday     (*.myworkdayjobs.com, *.myworkdaysite.com)
  - Oracle Cloud / Fusion HCM (*.oraclecloud.com)
  - iCIMS       (*.icims.com)
  - Ashby       (jobs.ashbyhq.com)
  - Amazon      (amazon.jobs)

Also supported, but only when requested explicitly with
"type": "sitemap" in companies.json:
  - XML job sitemaps — for JS-rendered sites that still publish a
    sitemap of their postings (e.g. careers.flowserve.com).

Everything else falls back to an HTML scraper with strict junk
filtering. The fallback is better than nothing but will miss jobs
on JavaScript-rendered sites and may occasionally misidentify
non-job links. The Actions log prints which method was used for
each company so you can tell at a glance what's reliable vs not.
"""

import json
import os
import re
import sys
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

import requests
from bs4 import BeautifulSoup

COMPANIES_FILE = "companies.json"
SEEN_FILE = "seen_jobs.json"
FILTERS_FILE = "filters.json"

# Ceiling on Telegram messages per run. A company changing its URL, or a
# fetcher that starts seeing postings it previously truncated, can turn a
# routine run into thousands of "new" jobs — enough for Telegram to
# rate-limit the bot. Past this we send one summary instead; the full list
# is always in the Actions log, and state still updates so the next run
# is back to normal.
MAX_ALERTS_PER_RUN = 50

# A company that suddenly shows dozens of "new" postings has almost always
# just started working — URL corrected, fetcher unblocked, truncation
# lifted — rather than having posted that many jobs since the last run.
# Past this many at once we record the board silently, exactly as a
# brand-new company is baselined, and send one summary instead.
RE_BASELINE_THRESHOLD = 30
HEADERS = {
    # Keep this reasonably current. iCIMS (and likely others) reject
    # noticeably stale User-Agents with a bare 405 Method Not Allowed,
    # which looks like a broken endpoint rather than a blocked client.
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    # Deliberately no Accept-Encoding: requests advertises exactly what it
    # can decode. Hardcoding "br" without the brotli package installed makes
    # servers send brotli that we then hand to the parser as binary garbage.
    "Connection": "keep-alive",
}

# ── Known junk phrases the fallback scraper should always ignore ──
JUNK_TITLES = {
    "apply now", "see details", "see jobs", "view job", "view jobs",
    "view all jobs", "view job openings", "view all", "search jobs",
    "search", "log in", "log back in!", "sign in", "sign up",
    "job search", "saved jobs", "saved", "careers", "careers home",
    "careers overview", "welcome page", "read more", "learn more",
    "explore", "explore options", "explore benefits", "explore all opportunities",
    "join our talent community", "feel the difference", "ai at work",
    "here", "english", "back", "next", "previous", "home",
    "about us", "about", "contact", "contact us", "privacy", "privacy policy",
    "terms", "terms of use", "cookie policy", "sitemap", "faq",
    "manage application", "your career", "why work here",
    "job categories", "job category", "all categories", "all locations",
    "clear filters", "reset", "subscribe", "newsletter",
    "account security", "application status", "my applications",
    "india jobs", "job alerts", "job alert", "set up job alerts",
    "sign up for job alerts", "create alert", "email me jobs",
    "university & early career", "experienced professionals",
    "our satellite locations", "working at",
}


def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return default


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def with_params(url, **overrides):
    """Return url with the given query params set (added or replaced)."""
    parts = urlparse(url)
    query = parse_qs(parts.query, keep_blank_values=True)
    for key, value in overrides.items():
        if value is None:
            query.pop(key, None)
        else:
            query[key] = [str(value)]
    return urlunparse(parts._replace(query=urlencode(query, doseq=True)))


# ═══════════════════════════════════════════════════════════════
# Filtering — location and role, applied to every company alike
# ═══════════════════════════════════════════════════════════════

US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC",
}

# State codes only count in a positional context — a bare "IN" or "OR"
# in free text is almost never Indiana or Oregon.
_STATE_CODES = "|".join(sorted(set(US_STATES.values())))
US_PATTERNS = [
    re.compile(r'\b(?:united states|usa|u\.s\.a?\.?)\b', re.I),
    re.compile(r'\bUS-[A-Z]{2}\b'),                      # iCIMS: US-CA-San Francisco
    re.compile(r',\s*(?:%s)\b' % _STATE_CODES),          # "Austin, TX"
    re.compile(r'^\s*(?:%s)\s*[-–]' % _STATE_CODES),     # "NY - Work from home"
    re.compile(r'[-–]\s*(?:%s)\s*$' % _STATE_CODES),     # "Chelmsford-MA"
    re.compile(r'\b(?:%s)\b' % "|".join(US_STATES), re.I),   # "Work At Home-Texas"
]

# Checked only after the US patterns, so "Birmingham, AL" stays a US hit.
NON_US_SIGNALS = [
    "india", "bengaluru", "bangalore", "hyderabad", "mumbai", "pune", "chennai",
    "gurgaon", "gurugram", "noida", "kolkata", "united kingdom", "england",
    "london", "manchester, uk", "edinburgh", "glasgow", "belfast", "dublin",
    "ireland", "france", "paris", "germany", "berlin", "munich", "frankfurt",
    "hamburg", "spain", "madrid", "barcelona", "italy", "milan", "rome",
    "netherlands", "amsterdam", "belgium", "brussels", "luxembourg",
    "switzerland", "zurich", "geneva", "austria", "vienna", "sweden",
    "stockholm", "norway", "oslo", "denmark", "copenhagen", "finland",
    "helsinki", "poland", "warsaw", "krakow", "czechia", "czech republic",
    "prague", "hungary", "budapest", "romania", "bucharest", "portugal",
    "lisbon", "greece", "athens", "turkey", "türkiye", "istanbul", "russia",
    "china", "shanghai", "beijing", "shenzhen", "hong kong", "taiwan",
    "taipei", "japan", "tokyo", "osaka", "korea", "seoul", "singapore",
    "malaysia", "kuala lumpur", "indonesia", "jakarta", "thailand",
    "bangkok", "vietnam", "philippines", "manila", "australia", "sydney",
    "melbourne", "brisbane", "perth", "new zealand", "auckland", "canada",
    "toronto", "vancouver", "montreal", "ottawa", "calgary", "mexico",
    "brazil", "sao paulo", "são paulo", "argentina", "buenos aires", "chile",
    "colombia", "bogota", "peru", "costa rica", "uae", "dubai", "abu dhabi",
    "saudi arabia", "riyadh", "qatar", "doha", "israel", "tel aviv", "egypt",
    "south africa", "johannesburg", "nigeria", "kenya", "morocco",
]


def load_filters():
    raw = load_json(FILTERS_FILE, {})
    loc = raw.get("location", {})
    roles = raw.get("roles", {})
    return {
        "location_enabled": loc.get("enabled", False),
        "countries": {c.upper() for c in loc.get("countries", [])},
        "include_unknown": loc.get("include_unknown", True),
        "roles_enabled": roles.get("enabled", False),
        "include": [s.lower() for s in roles.get("include", [])],
        "exclude": [s.lower() for s in roles.get("exclude", [])],
        "search_keywords": raw.get("search_keywords", []),
    }


def classify_location(text, country_code=None):
    """
    Return "US", "OTHER", or "UNKNOWN".

    Prefers an explicit country code where the platform gives us one
    (Oracle and Amazon do); otherwise reads the human-readable location
    string, checking US signals before foreign ones so that genuinely
    ambiguous city names like Birmingham resolve correctly when the
    state is present.
    """
    if country_code:
        code = country_code.strip().upper()
        if code in ("US", "USA", "UNITED STATES"):
            return "US"
        if code:
            return "OTHER"

    if not text:
        return "UNKNOWN"
    text = text.strip()
    if not text:
        return "UNKNOWN"

    for pattern in US_PATTERNS:
        if pattern.search(text):
            return "US"

    lowered = text.lower()
    for signal in NON_US_SIGNALS:
        if signal in lowered:
            return "OTHER"

    return "UNKNOWN"


def location_allowed(job, filters):
    if not filters["location_enabled"] or not filters["countries"]:
        return True
    verdict = classify_location(job.get("location"), job.get("country_code"))
    if verdict == "UNKNOWN":
        return filters["include_unknown"]
    return verdict in filters["countries"]


def role_allowed(title, filters):
    if not filters["roles_enabled"]:
        return True
    lowered = (title or "").lower()
    if filters["include"] and not any(k in lowered for k in filters["include"]):
        return False
    if any(k in lowered for k in filters["exclude"]):
        return False
    return True


def apply_filters(jobs, filters):
    """Drop jobs that fail the role or location rules. Returns (kept, stats)."""
    kept = {}
    dropped_role = dropped_location = 0
    for job_id, job in jobs.items():
        if not role_allowed(job.get("title"), filters):
            dropped_role += 1
            continue
        if not location_allowed(job, filters):
            dropped_location += 1
            continue
        kept[job_id] = job
    return kept, {"role": dropped_role, "location": dropped_location}


# ═══════════════════════════════════════════════════════════════
# Platform detection — each returns parsed parts or None
# ═══════════════════════════════════════════════════════════════

def get_greenhouse_slug(url):
    m = re.search(r"greenhouse\.io/([a-zA-Z0-9\-_]+)", url)
    return m.group(1) if m else None


def get_lever_slug(url):
    m = re.search(r"lever\.co/([a-zA-Z0-9\-_]+)", url)
    return m.group(1) if m else None


def get_workday_parts(url):
    """
    Workday URLs:
      https://{tenant}.wd{N}.myworkdayjobs.com/{site}
      https://{tenant}.wd{N}.myworkdayjobs.com/en-US/{site}
      https://wd{N}.myworkdaysite.com/recruiting/{tenant}/{site}
    """
    m = re.search(
        r'https?://([a-zA-Z0-9\-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}(?:-[A-Z]{2})?/)?([a-zA-Z0-9\-_]+)',
        url
    )
    if m:
        return m.group(1), m.group(2), m.group(3)
    m = re.search(
        r'https?://(wd\d+)\.myworkdaysite\.com/recru
