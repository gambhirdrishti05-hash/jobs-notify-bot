"""
Job Alert Bot — checks company career pages for new postings.

Supported platforms (reliable, uses structured API):
  - Greenhouse  (boards-api.greenhouse.io)
  - Lever       (api.lever.co)
  - Workday     (*.myworkdayjobs.com, *.myworkdaysite.com)
  - Oracle Cloud / Fusion HCM (*.oraclecloud.com)
  - iCIMS       (*.icims.com)
  - Ashby       (jobs.ashbyhq.com)

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
import requests
from bs4 import BeautifulSoup

COMPANIES_FILE = "companies.json"
SEEN_FILE = "seen_jobs.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

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
        r'https?://(wd\d+)\.myworkdaysite\.com/recruiting/([a-zA-Z0-9\-_]+)/([a-zA-Z0-9\-_]+)',
        url
    )
    if m:
        dc, tenant, site = m.group(1), m.group(2), m.group(3)
        return tenant, dc, site
    return None


def get_oracle_cloud_parts(url):
    """Oracle Cloud (Fusion HCM): *.oraclecloud.com with /sites/{SITE}/"""
    m = re.search(r'https?://([a-zA-Z0-9\-]+)\.fa\.(?:[a-zA-Z0-9]+\.)?oraclecloud\.com', url)
    site_m = re.search(r'sites/([A-Za-z0-9_]+)', url)
    if not m or not site_m:
        return None
    host = re.search(r'https?://([a-zA-Z0-9\-\.]+\.oraclecloud\.com)', url).group(1)
    return host, site_m.group(1)


def get_icims_parts(url):
    """
    iCIMS URLs:
      https://{company}.icims.com/jobs/search?ss=1
      https://careers-{company}.icims.com/jobs/search?ss=1
    Returns the base URL (everything before /jobs/) or None.
    """
    if "icims.com" not in url:
        return None
    m = re.search(r'(https?://[a-zA-Z0-9\-\.]+\.icims\.com)', url)
    return m.group(1) if m else None


def get_ashby_slug(url):
    """Ashby: https://jobs.ashbyhq.com/{company}"""
    m = re.search(r'jobs\.ashbyhq\.com/([a-zA-Z0-9\-_]+)', url)
    return m.group(1) if m else None


# ═══════════════════════════════════════════════════════════════
# Fetchers — one per platform
# ═══════════════════════════════════════════════════════════════

def fetch_greenhouse(slug):
    api = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    r = requests.get(api, headers=HEADERS, timeout=20)
    r.raise_for_status()
    jobs = r.json().get("jobs", [])
    return {str(j["id"]): {"title": j["title"], "url": j["absolute_url"]} for j in jobs}


def fetch_lever(slug):
    api = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    r = requests.get(api, headers=HEADERS, timeout=20)
    r.raise_for_status()
    jobs = r.json()
    return {j["id"]: {"title": j["text"], "url": j["hostedUrl"]} for j in jobs}


def fetch_workday(tenant, dc, site, base_url):
    api = f"https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    jobs = {}
    offset = 0
    limit = 20
    while True:
        r = requests.post(
            api,
            json={"appliedFacets": {}, "limit": limit, "offset": offset, "searchText": ""},
            headers={**HEADERS, "Content-Type": "application/json"},
            timeout=20
        )
        r.raise_for_status()
        data = r.json()
        postings = data.get("jobPostings", [])
        if not postings:
            break
        for p in postings:
            path = p.get("externalPath", "")
            job_id = path or p.get("title", "")
            jobs[job_id] = {
                "title": p.get("title", "Untitled"),
                "url": f"https://{tenant}.{dc}.myworkdayjobs.com{path}"
            }
        total = data.get("total", 0)
        offset += limit
        if offset >= total:
            break
    return jobs


def fetch_oracle_cloud(host, site_number):
    api = f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
    jobs = {}
    offset = 0
    limit = 25
    while True:
        finder = (
            f"findReqs;siteNumber={site_number},limit={limit},offset={offset},"
            f"sortBy=POSTING_DATES_DESC"
        )
        params = {"onlyData": "true", "finder": finder}
        r = requests.get(api, params=params, headers=HEADERS, timeout=20)
        r.raise_for_status()
        data = r.json()
        items = data.get("items", [])
        if not items:
            break
        reqs = items[0].get("requisitionList", []) if items else []
        if not reqs:
            break
        for job in reqs:
            job_id = job.get("Id") or job.get("RequisitionNumber")
            title = job.get("Title", "Untitled")
            req_num = job.get("RequisitionNumber", "")
            job_url = f"https://{host}/hcmUI/CandidateExperience/en/sites/{site_number}/job/{req_num}"
            jobs[str(job_id)] = {"title": title, "url": job_url}
        offset += limit
        if len(reqs) < limit:
            break
    return jobs


def fetch_icims(base_url):
    """
    iCIMS career portals expose a /careers/jobs endpoint that returns
    HTML with structured job listings. The links follow a consistent
    pattern: /jobs/{job_id}/job — much more reliable than the generic
    fallback since we can filter on URL structure instead of guessing
    from link text.
    """
    search_url = f"{base_url}/jobs/search?ss=1&searchRelation=keyword_all"
    r = requests.get(search_url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    jobs = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        # iCIMS job links look like /jobs/1234/job or /jobs/1234/title
        m = re.search(r'/jobs/(\d+)/', href)
        if not m:
            continue
        job_id = m.group(1)
        text = a.get_text(strip=True)
        # Clean up iCIMS's "TitleActual Job Name" prefix pattern
        text = re.sub(r'^(Job\s*)?Title', '', text).strip()
        if not text or len(text) < 4 or text.lower() in JUNK_TITLES:
            continue
        full_url = href if href.startswith("http") else requests.compat.urljoin(base_url, href)
        if job_id not in jobs:  # first link text for this ID wins
            jobs[job_id] = {"title": text, "url": full_url}
    return jobs


def fetch_ashby(slug):
    """Ashby has a proper public JSON API, same category as Greenhouse/Lever."""
    api = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    r = requests.get(api, headers=HEADERS, timeout=20)
    r.raise_for_status()
    data = r.json()
    jobs = {}
    for job in data.get("jobs", []):
        job_id = job.get("id", "")
        title = job.get("title", "Untitled")
        job_url = f"https://jobs.ashbyhq.com/{slug}/{job_id}"
        jobs[job_id] = {"title": title, "url": job_url}
    return jobs


def is_junk_title(text):
    """Check if a scraped title is likely navigation junk, not a real job."""
    cleaned = text.strip().lower()
    # Exact match against known junk
    if cleaned in JUNK_TITLES:
        return True
    # Starts with known junk prefix
    for junk in JUNK_TITLES:
        if cleaned.startswith(junk):
            return True
    # Too short to be a real job title
    if len(cleaned) < 8:
        return True
    # All one word (real job titles are almost always 2+ words)
    if len(cleaned.split()) < 2:
        return True
    # Contains no letters (just symbols/numbers)
    if not re.search(r'[a-zA-Z]', cleaned):
        return True
    return False


def fetch_generic(url):
    """
    Best-effort fallback for unrecognized career sites.
    Now with much stricter filtering to reduce junk alerts.
    """
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    jobs = {}
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        href = a["href"]
        if not text or len(text) < 8 or len(text) > 120:
            continue
        # Clean up common prefixes iCIMS-style sites add
        text = re.sub(r'^(Job\s*)?Title\s*', '', text).strip()
        if is_junk_title(text):
            continue
        # Must have BOTH a title-like signal AND a link-like signal
        # (old version accepted either/or, which was too loose)
        title_signal = re.search(
            r"\b(analyst|manager|engineer|associate|specialist|coordinator|"
            r"director|lead|intern|consultant|scientist|developer|designer|"
            r"administrator|architect|accountant|advisor|assistant|attorney|"
            r"nurse|physician|technician|supervisor|representative|officer|"
            r"recruiter|paralegal|planner|buyer|auditor|controller)\b",
            text, re.IGNORECASE
        )
        link_signal = re.search(
            r"(job[s]?/|career|position|opening|requisition|req|posting|apply|vacancies)",
            href, re.IGNORECASE
        )
        if title_signal and link_signal:
            full_url = href if href.startswith("http") else requests.compat.urljoin(url, href)
            job_id = full_url
            jobs[job_id] = {"title": text, "url": full_url}
    return jobs


# ═══════════════════════════════════════════════════════════════
# Main router — detects platform, calls the right fetcher
# ═══════════════════════════════════════════════════════════════

def fetch_company_jobs(company):
    url = company["url"]
    name = company["name"]

    gh_slug = get_greenhouse_slug(url)
    lv_slug = get_lever_slug(url)
    wd_parts = get_workday_parts(url)
    oc_parts = get_oracle_cloud_parts(url)
    icims_base = get_icims_parts(url)
    ashby_slug = get_ashby_slug(url)

    try:
        if gh_slug:
            print(f"  [Greenhouse] {name}")
            return fetch_greenhouse(gh_slug)
        elif lv_slug:
            print(f"  [Lever] {name}")
            return fetch_lever(lv_slug)
        elif wd_parts:
            tenant, dc, site = wd_parts
            print(f"  [Workday] {name} (tenant={tenant}, dc={dc}, site={site})")
            return fetch_workday(tenant, dc, site, url)
        elif oc_parts:
            host, site_number = oc_parts
            print(f"  [Oracle Cloud] {name} (host={host}, site={site_number})")
            return fetch_oracle_cloud(host, site_number)
        elif icims_base:
            print(f"  [iCIMS] {name} (base={icims_base})")
            return fetch_icims(icims_base)
        elif ashby_slug:
            print(f"  [Ashby] {name} (slug={ashby_slug})")
            return fetch_ashby(ashby_slug)
        else:
            # Safety net warnings for near-misses
            if "myworkdayjobs.com" in url or "myworkdaysite.com" in url:
                print(f"  [WARN] {name}: looks like Workday but URL wasn't recognized — check URL format", file=sys.stderr)
            elif "oraclecloud.com" in url:
                print(f"  [WARN] {name}: looks like Oracle Cloud but URL wasn't recognized — check URL format", file=sys.stderr)
            elif "icims.com" in url:
                print(f"  [WARN] {name}: looks like iCIMS but URL wasn't recognized — check URL format", file=sys.stderr)
            print(f"  [Fallback scraper] {name} — results may be unreliable")
            return fetch_generic(url)
    except Exception as e:
        print(f"  [ERROR] {name}: {e}", file=sys.stderr)
        return None


def send_telegram(message):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_ids_raw = os.environ.get("TELEGRAM_CHAT_ID", "")
    chat_ids = [c.strip() for c in chat_ids_raw.split(",") if c.strip()]

    if not token or not chat_ids:
        print("[WARN] Telegram credentials not set — printing instead:")
        print(message)
        return

    api = f"https://api.telegram.org/bot{token}/sendMessage"
    for chat_id in chat_ids:
        try:
            requests.post(api, data={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": False
            }, timeout=20)
        except Exception as e:
            print(f"  [ERROR] Failed to send to chat_id {chat_id}: {e}")


def main():
    companies = load_json(COMPANIES_FILE, [])
    seen = load_json(SEEN_FILE, {})

    total_new = 0

    for company in companies:
        name = company["name"]
        print(f"Checking {name}...")
        current_jobs = fetch_company_jobs(company)

        if current_jobs is None:
            continue

        prev_ids = set(seen.get(name, {}).keys())
        current_ids = set(current_jobs.keys())
        new_ids = current_ids - prev_ids

        if name not in seen:
            print(f"  First run — recording {len(current_ids)} postings as baseline.")
            seen[name] = current_jobs
            continue

        for job_id in new_ids:
            job = current_jobs[job_id]
            msg = f"🆕 <b>{name}</b>\n{job['title']}\n{job['url']}"
            send_telegram(msg)
            total_new += 1
            print(f"  NEW: {job['title']}")

        seen[name] = current_jobs

    save_json(SEEN_FILE, seen)
    print(f"\nDone. {total_new} new posting(s) found.")


if __name__ == "__main__":
    main()
