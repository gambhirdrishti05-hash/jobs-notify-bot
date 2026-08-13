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
- Anything else, just paste their normal careers page URL. It'll use the fallback
  scraper, which is less reliable — see note below.

### 6. Turn it on
- Push everything to GitHub. The workflow runs automatically every 2 hours.
- To test immediately: go to the repo's **Actions** tab → "Check job postings" →
  **Run workflow** button.
- First run for each company just records what's currently posted (no alert spam) —
  you'll only get pinged for postings that show up *after* that.

## Known limitations

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
