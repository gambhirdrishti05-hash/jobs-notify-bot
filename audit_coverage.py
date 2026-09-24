"""
Coverage audit for the keyword track. Read-only, writes nothing.

The bot pre-filters server-side with filters.json search_keywords, which
keeps runs fast but means it only ever sees a slice of each board. This
script answers the obvious question: is anything being missed?

For each company it fetches twice in the same run:
  narrow  - exactly what the bot normally tracks
  wide    - the entire board, no search keywords

Then it applies your real roles/location rules to the wide set. Anything
that passes those rules but is absent from the narrow set is a posting
the bot SHOULD have alerted on and didn't.

Run it from the Actions tab. To audit one company instead of all 48:
set the AUDIT_COMPANIES env var, e.g. "Analog Devices" or a
comma-separated list.
"""

import os
import sys

from check_jobs import (
    fetch_company_jobs,
    load_filters,
    load_json,
    location_allowed,
    role_allowed,
    with_params,
)

COMPANIES_FILE = "companies.json"
SHOW_MISSED = 20   # per company, so the log stays readable


def main():
    companies = load_json(COMPANIES_FILE, [])
    filters = load_filters()

    only = os.environ.get("AUDIT_COMPANIES", "").strip()
    if only:
        wanted = {c.strip().lower() for c in only.split(",") if c.strip()}
        companies = [c for c in companies if c["name"].lower() in wanted]
        if not companies:
            print(f"[ERROR] No company matched {only!r}", file=sys.stderr)
            sys.exit(1)

    # Same filters, minus the narrowing. Keeps include/exclude/countries
    # present so we can apply them ourselves further down.
    wide_filters = dict(filters)
    wide_filters["search_keywords"] = []
    wide_filters["roles_enabled"] = False
    wide_filters["location_enabled"] = False

    totals = {"narrow": 0, "should": 0, "missed": 0}
    problem_companies = []

    for company in companies:
        name = company["name"]
        print(f"\n=== {name} ===")

        narrow = fetch_company_jobs(company, filters)
        if narrow is None:
            print("  [ERROR] narrow fetch failed, skipping")
            continue

        wide_company = dict(company)
        wide_company["url"] = with_params(company["url"], q=None)
        wide = fetch_company_jobs(wide_company, wide_filters)
        if wide is None:
            print("  [ERROR] wide fetch failed, skipping")
            continue

        should = {
            jid: job for jid, job in wide.items()
            if role_allowed(job.get("title"), filters)
            and location_allowed(job, filters)
        }
        missed = {jid: job for jid, job in should.items() if jid not in narrow}

        totals["narrow"] += len(narrow)
        totals["should"] += len(should)
        totals["missed"] += len(missed)

        pct = (len(missed) / len(should) * 100) if should else 0
        print(f"  whole board: {len(wide)}")
        print(f"  bot tracks:  {len(narrow)}")
        print(f"  should track (whole board, your rules applied): {len(should)}")
        print(f"  MISSED: {len(missed)}  ({pct:.0f}% of what it should catch)")

        if missed:
            problem_companies.append((name, len(missed), len(should)))
            for job in list(missed.values())[:SHOW_MISSED]:
                loc = job.get("location") or "?"
                print(f"    - {job['title']}  [{loc}]")
            if len(missed) > SHOW_MISSED:
                print(f"    ... and {len(missed) - SHOW_MISSED} more")

    print("\n" + "=" * 60)
    print(f"TOTAL tracked: {totals['narrow']}")
    print(f"TOTAL should track: {totals['should']}")
    print(f"TOTAL missed: {totals['missed']}")
    if problem_companies:
        print("\nWorst offenders:")
        for name, miss, should in sorted(problem_companies, key=lambda x: -x[1]):
            print(f"  {name}: missing {miss} of {should}")
    else:
        print("\nNo gaps found. The keyword pre-filter is not costing you anything.")


if __name__ == "__main__":
    main()
