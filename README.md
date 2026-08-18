# Job Alert Bot

Watches company career pages and pings you on Telegram the moment a new role goes up.

## Setup (about 10-15 minutes, one time)

### 1. Create a Telegram bot
- Open Telegram, search for **@BotFather**, start a chat.
- Send `/newbot`, follow the prompts, give it any name.
- BotFather gives you a **token** that looks like `123456:ABC-...`. Save it.

### 2. Get your chat ID
- Search for **@userinfobot** on Telegram, start a chat with it, send any message.
- It replies with your numeric **chat ID**. Save it.

### 3. Create a GitHub repo
- Create a new repo (can be private).
- Upload all these files into it (companies.json, check_jobs.py, requirements.txt,
  README.md, and the `.github/workflows/check-jobs.yml` file — keep the folder structure).

### 4. Add your secrets
- In the repo: Settings → Secrets and variables → Actions → New repository secret.
- Add `TELEGRAM_BOT_TOKEN` (from step 1).
- Add `TELEGRAM_CHAT_ID` (from step 2).

### 5. Add companies
Open `companies.json` and add one entry per company:

```json
[
  { "name": "Amazon", "url": "https://boards.greenhouse.io/amazon" },
  { "name": "Some Startup", "url": "https://careers.somestartup.com" }
]
```

Delete the example entry. To add a company later, just edit this file and push —
no code changes needed.

**Finding the right URL:**
- If a company uses Greenhouse, the URL looks like `boards.greenhouse.io/companyname`
  or their careers page redirects there — check the browser address bar.
- If Lever, it looks like `jobs.lever.co/companyname`.
- If Workday, the URL looks like `companyname.wd5.myworkdayjobs.com/SomeCareerSite`
  (the `wd` number varies by company — could be wd1, wd3, wd5, etc). This covers
  a large share of big employers (NVIDIA, HP, Schwab, and many others run on it).
  Just paste the URL as-is from your browser, no need to figure out the parts yourself.
- If iCIMS, it looks like `companyname.icims.com/jobs/search?ss=1`.
- Anything else, just paste their normal careers page URL. It'll use the fallback
  scraper, which is less reliable — see note below.

**A company's own careers page is often a skin over one of the above.** Before
settling for the fallback scraper, click through to an actual job posting and
look at where "Apply" goes — `careers.icf.com` turned out to be Workday
underneath, and `aptean.com/careers` turned out to be iCIMS. Using the
underlying URL is far more reliable than scraping the pretty front-end.

**Narrowing a company that posts a lot:** for Workday, add `?q=keyword` to the
URL and it's passed through to the search. For Amazon, add `?base_query=keyword`.
Without a filter, employers like CVS (19,000 postings) or JPMorgan (7,000) will
dominate your alerts.

**Sites that publish a job sitemap:** if a career site renders jobs in JavaScript
but has an XML sitemap of postings, add `"type": "sitemap"` alongside the URL and
point it at the sitemap:

```json
{ "name": "Flowserve", "url": "https://careers.flowserve.com/sitemap.xml", "type": "sitemap" }
```

### 6. Turn it on
- Push everything to GitHub. The workflow runs automatically every 2 hours.
- To test immediately: go to the repo's **Actions** tab → "Check job postings" →
  **Run workflow** button.
- First run for each company just records what's currently posted (no alert spam) —
  you'll only get pinged for postings that show up *after* that.

## Filtering what you get alerted about

`filters.json` applies to every company. Edit it and push — no code changes.

**Location.** Only US postings get through by default:

```json
"location": { "enabled": true, "countries": ["US"], "include_unknown": true }
```

Where a platform gives us a real country field we use it (Oracle, Amazon), and
Workday gets a country facet applied server-side where the tenant exposes one.
Everything else is read from the location text — `US-CA-San Francisco`,
`Austin, TX`, `Work At Home-Texas` and similar all resolve correctly.

`include_unknown` covers postings whose location we can't classify — `2 Locations`,
`Hybrid`, or sites that publish no location at all. It defaults to `true` so you
don't silently miss a real US role; set it to `false` if you'd rather have a
tighter feed and accept losing some.

**Roles.** A job's *title* must match one of `roles.include` and none of
`roles.exclude`. `"analyst"` on its own covers business analyst, data analyst,
research analyst and the rest, so the list stays short. The `exclude` list is
what keeps unrelated analyst roles (lab, clinical, security, credit) out — add
to it whenever something irrelevant slips through.

**`search_keywords`** is a separate, coarser list sent to the search box of
platforms that have one (Workday, Oracle, Amazon). It exists so we pull a few
hundred candidate postings instead of every job the company has — Mass General
Brigham is 2,000+ postings but ~25 analyst roles. Keep it *broader* than your
title rules; the title rules do the precise work. If you add a role family to
`roles.include`, add a matching term here too or those jobs will never be
fetched in the first place.

Every run prints what it dropped, so you can see the filters working:

```
Checking Mass General Brigham...
  Filtered 2085 → 25 (dropped 2060 on role, 0 on location)
```

## Known limitations

- **Alerts are capped at 50 per run** (`MAX_ALERTS_PER_RUN` in `check_jobs.py`).
  Past that you get one summary message with a per-company breakdown, and the
  full list stays in the Actions log. This exists because a company changing
  its careers URL can otherwise turn one run into thousands of messages and
  get the bot rate-limited by Telegram.
- **Sites that block datacenter traffic can't be watched from here.** Cloudflare
  and Akamai bot protection reject GitHub Actions runners outright (HTTP 403, or
  a JavaScript challenge page instead of content) no matter what headers are
  sent. Known cases: Credit One Bank, UC Davis, Hertz. Use the company's own
  email job alerts or a Google Alert for these.
- **Keep the User-Agent current.** iCIMS answers noticeably stale User-Agents
  with a bare `405 Method Not Allowed`, which reads like a broken endpoint
  rather than a blocked client. If several iCIMS companies start returning 405
  at once, bump the Chrome version in `HEADERS` first.

- **Workday quirks:** Workday's job data isn't officially documented as a public
  API — it's the same internal endpoint the career page itself calls in the
  browser, just called directly instead. It's stable and widely used this way,
  but if Workday changes something on their end, this could break without
  warning. If a Workday company stops showing alerts, that's the first thing
  to check.
- **Companies with custom career sites (not Greenhouse/Lever/Workday):** the fallback
  scraper guesses which links are job postings based on the text and URL. It can
  miss postings or, rarely, flag something that isn't a job. If a company you
  care about isn't triggering alerts correctly, check the "Actions" tab logs —
  it prints what it found.
- **Sites that load jobs via JavaScript** (the job list isn't in the raw HTML)
  won't work with this fallback at all. If a company falls into this bucket, say
  so and it needs a custom rule.
- Runs every 2 hours by default. Change the `cron` line in the workflow file if
  you want it more or less frequent (GitHub Actions free tier gives you 2,000
  minutes/month — this uses only a few minutes per run, so frequent runs are fine).
