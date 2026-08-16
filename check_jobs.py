"""
Checks every company in companies.json for new job postings.
Sends a Telegram message for anything new since the last run.

How it works:
- Greenhouse and Lever companies: hits their public JSON API (fast, reliable)
- Everything else: does a best-effort scrape of the careers page and
  looks for link text that looks like a job title. Less reliable —
  if a company on a custom career site stops showing new alerts,
  that's the first place to check.
"""

import json
import os
import re
import sys
import requests
from bs4 import BeautifulSoup

COMPANIES_FILE = "companies.json"
SEEN_FILE = "seen_jobs.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (job-alert-bot)"}


def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return default


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def get_greenhouse_slug(url):
    m = re.search(r"greenhouse\.io/([a-zA-Z0-9\-_]+)", url)
    return m.group(1) if m else None


def get_lever_slug(url):
    m = re.search(r"lever\.co/([a-zA-Z0-9\-_]+)", url)
    return m.group(1) if m else None


def get_workday_parts(url):
    """
    Workday career URLs look like:
      https://{tenant}.wd{N}.myworkdayjobs.com/{site}
      https://{tenant}.wd{N}.myworkdayjobs.com/en-US/{site}
      https://wd{N}.myworkdaysite.com/recruiting/{tenant}/{site}   (newer domain)
    Returns (tenant, dc, site) or None if this isn't a Workday URL.
    """
    # Older/most common pattern: tenant.wdN.myworkdayjobs.com
    m = re.search(
        r'https?://([a-zA-Z0-9\-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-zA-Z\-]+/)?([a-zA-Z0-9\-_]+)',
        url
    )
    if m:
        return m.group(1), m.group(2), m.group(3)

    # Newer pattern: wdN.myworkdaysite.com/recruiting/tenant/site
    m = re.search(
        r'https?://(wd\d+)\.myworkdaysite\.com/recruiting/([a-zA-Z0-9\-_]+)/([a-zA-Z0-9\-_]+)',
        url
    )
    if m:
        dc, tenant, site = m.group(1), m.group(2), m.group(3)
        return tenant, dc, site

    return None


def get_oracle_cloud_parts(url):
    """
    Oracle Cloud (Fusion HCM) career URLs look like:
      https://{tenant}.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/{SITE_NUMBER}/jobs
      https://{tenant}.fa.{region}.oraclecloud.com/hcmUI/CandidateExperience/en/sites/{SITE_NUMBER}/jobs
    Returns (tenant, site_number) or None.
    """
    m = re.search(r'https?://([a-zA-Z0-9\-]+)\.fa\.(?:[a-zA-Z0-9]+\.)?oraclecloud\.com', url)
    site_m = re.search(r'sites/([A-Za-z0-9_]+)', url)
    if not m or not site_m:
        return None
    tenant_host = re.search(r'https?://([a-zA-Z0-9\-\.]+\.oraclecloud\.com)', url).group(1)
    return tenant_host, site_m.group(1)


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
    """
    Best-effort Oracle Cloud (Fusion HCM Recruiting) fetcher.
    NOTE: unlike the Workday/Greenhouse/Lever fetchers, this pattern is
    reconstructed from Oracle's own docs and third-party references, not
    verified against a live site from here. If this returns nothing or
    errors, that's a signal the exact parameters need adjusting for that
    specific company's Oracle Cloud instance — not necessarily that the
    company has no jobs.
    """
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


def fetch_generic(url):
    """
    Best-effort fallback for career sites that aren't Greenhouse or Lever.
    Looks for links whose visible text looks like a job title.
    Not perfect — some sites render jobs via JavaScript and won't show
    up here at all. If that happens for a company you care about,
    flag it and we'll build a site-specific rule for it.
    """
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    jobs = {}
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        href = a["href"]
        if not text or len(text) < 4 or len(text) > 100:
            continue
        # Heuristic: job links usually contain words like these, or the
        # link itself points somewhere with "job" / "career" / "position" in it
        title_signal = re.search(
            r"\b(analyst|manager|engineer|associate|specialist|coordinator|"
            r"director|lead|intern|consultant|scientist|developer|designer)\b",
            text, re.IGNORECASE
        )
        link_signal = re.search(r"(job|career|position|opening|req)", href, re.IGNORECASE)
        if title_signal or link_signal:
            full_url = href if href.startswith("http") else requests.compat.urljoin(url, href)
            job_id = full_url  # use the URL itself as the unique id
            jobs[job_id] = {"title": text, "url": full_url}
    return jobs


def fetch_company_jobs(company):
    url = company["url"]
    name = company["name"]
    gh_slug = get_greenhouse_slug(url)
    lv_slug = get_lever_slug(url)
    wd_parts = get_workday_parts(url)
    oc_parts = get_oracle_cloud_parts(url)
    try:
        if gh_slug:
            return fetch_greenhouse(gh_slug)
        elif lv_slug:
            return fetch_lever(lv_slug)
        elif wd_parts:
            tenant, dc, site = wd_parts
            return fetch_workday(tenant, dc, site, url)
        elif oc_parts:
            host, site_number = oc_parts
            return fetch_oracle_cloud(host, site_number)
        else:
            # Safety net: this URL looks like it MIGHT be Workday or Oracle
            # Cloud but didn't match our known patterns — likely a URL shape
            # we haven't seen before (e.g. a direct job link instead of the
            # base career page, or a bare URL with no site name). Flag it
            # clearly instead of silently treating it as "just another
            # custom site" and falling back to the unreliable scraper.
            if "myworkdayjobs.com" in url or "myworkdaysite.com" in url:
                print(f"  [WARN] {name}: looks like Workday but URL format wasn't recognized — check the URL (should point to the base career site, not a specific job posting)", file=sys.stderr)
            elif "oraclecloud.com" in url:
                print(f"  [WARN] {name}: looks like Oracle Cloud but URL format wasn't recognized — check the URL", file=sys.stderr)
            return fetch_generic(url)
    except Exception as e:
        print(f"  [ERROR] {name}: {e}", file=sys.stderr)
        return None  # signal failure — don't wipe out seen state on a broken fetch


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
            continue  # fetch failed — skip, don't touch seen state for this company

        prev_ids = set(seen.get(name, {}).keys())
        current_ids = set(current_jobs.keys())
        new_ids = current_ids - prev_ids

        # First time seeing this company: record baseline, don't spam
        # you with every existing posting.
        if name not in seen:
            print(f"  First run for {name} — recording {len(current_ids)} postings as baseline.")
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
