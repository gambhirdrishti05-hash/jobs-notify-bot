Job Alert Bot

Watches company career pages and sends a Telegram message when a new role goes up, so you can apply early.

How it works in one minute

* A GitHub Actions workflow runs the script every hour.
* Your company list is split into two halves, chosen by company name. Odd hours check one half, even hours check the other. Every company is still checked every 2 hours, but each run puts half the load on career sites.
* For each company the script reads the job list, applies filters.json, and compares the result with seen_jobs.json. Anything not seen before is new and gets sent to Telegram.
* seen_jobs.json is updated and committed back to the repo after each run. It only holds what is live right now, so it does not grow forever.

Setup (about 10 to 15 minutes, one time)

1. Create a Telegram bot
* Open Telegram, search for @BotFather, start a chat.
* Send /newbot and follow the prompts.
* BotFather gives you a token that looks like 123456:ABC-.... Save it.

2. Get your chat ID
* Search for @userinfobot on Telegram and send it any message.
* It replies with your numeric chat ID. Save it.
* To alert more than one person, put several IDs in the secret separated by commas.

3. Create a GitHub repo
* Upload these files and keep the folder structure: companies.json, filters.json, check_jobs.py, requirements.txt, README.md, and .github/workflows/check-jobs.yml.
* Warning: if the repo is public, everyone can read every file in it. Never put your bot token or chat ID in any file. They go in Secrets only (next step).

4. Add your secrets
* In the repo: Settings, Secrets and variables, Actions, New repository secret.
* Add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.
* Treat any URL that contains ?token= like a password.

5. Add companies
Open companies.json and add one entry per company:

[
  { "name": "Amazon", "url": "https://www.amazon.jobs/en/search?base_query=analyst" },
  { "name": "Some Startup", "url": "https://careers.somestartup.com" }
]

To add a company later, edit this file and push. No code changes needed.

6. Turn it on
* Push everything to GitHub. The workflow is set to run every hour at 23 minutes past.
* To test right away: Actions tab, "Check job postings", Run workflow.
* The first run for each company only records what is posted today, so you are not flooded. You only get alerts for postings that appear after that.

Finding the right URL

The bot recognizes these platforms from the URL and uses a proper method for each. Paste the URL as it appears in your browser.

* Greenhouse: boards.greenhouse.io/companyname
* Lever: jobs.lever.co/companyname
* Ashby: jobs.ashbyhq.com/companyname
* Workday: companyname.wd5.myworkdayjobs.com/SiteName (wd1, wd3, wd5 and others all work)
* Oracle Cloud: a URL containing oraclecloud.com and /sites/SiteName
* iCIMS: companyname.icims.com/jobs/search?ss=1
* Amazon: amazon.jobs, add ?base_query=keyword to narrow it
* Avature: a URL ending in /SearchJobs (Deloitte, Siemens and St. Jude work this way)
* Phenom People: pages like /search-jobs or /job-search-results. The bot calls the site's own job list endpoint.
* Anything else uses the fallback scraper (see limitations below)

Tips
* A company's own careers page is often a skin over one of the platforms above. Click into a real posting and see where Apply goes. careers.icf.com turned out to be Workday, and aptean.com/careers turned out to be iCIMS. The underlying URL is far more reliable.
* Some sites look like Phenom but are not. If the Phenom method fails, the bot automatically falls back to the generic scraper.
* To force Phenom on a site the bot does not detect, add "type": "phenom" to its entry.
* Sitemap sites: if a site draws jobs with JavaScript but publishes an XML sitemap of postings, add "type": "sitemap" and point the URL at the sitemap.
* Narrow big employers. For Workday add ?q=keyword to the URL. Without a filter, companies like CVS (19,000 postings) will bury everything else.
* Keep your keyword in the URL for fallback sites, since the scraper only sees whatever that exact page shows.

Filtering what you get alerted about

filters.json applies to every company. Edit it and push.

* Location: only US postings by default. include_unknown keeps postings whose location cannot be read, so you do not silently miss a real US role. Set it to false for a tighter feed.
* Roles: a title must match one word in roles.include and none in roles.exclude. The exclude list also holds seniority words (senior, principal, manager and similar) so you mostly see early and mid level roles.
* search_keywords: a broader list sent to the search box on platforms that have one (Workday, Oracle, Amazon, Avature). Keep it wider than your title rules. If you add a role family to roles.include, add a matching word here, or those jobs are never fetched.
* Filters only read the job title. The bot never opens the job description, so things like "no sponsorship" or "clearance required" buried in the text cannot be filtered. Check each posting yourself before applying. Deloitte is the exception, since it puts clearance terms in the title.

Every run prints what it dropped:

Checking Mass General Brigham...
  Filtered 2085 to 25 (dropped 2060 on role, 0 on location)

What protects you from floods and errors

* Alerts are capped at 50 per run (MAX_ALERTS_PER_RUN). Extras are queued, not lost, and come through on later runs. One summary message lists what is waiting.
* Alerts are dealt out one company at a time, so a company with 200 new postings cannot push everyone else out.
* If a company shows more than 30 new postings at once (RE_BASELINE_THRESHOLD), it almost always means a broken board just started working. The bot records them silently and sends one summary instead.
* Requests are spaced out and retried with a growing wait on HTTP 429, 502 and 503. If a provider such as Workday keeps refusing, the bot stops retrying it for the rest of that run so one blocked provider cannot burn the whole run.
* If 10 percent or more of the companies checked in a run fail (at least 5), you get a Telegram warning. Those boards were not checked, so postings are delayed, not lost.
* Two runs never overlap. A queue (concurrency group) holds the second one. The final step pulls and retries before pushing, so edits you make in the browser during a run do not break it.
* The same chat ID listed twice is only messaged once.

Known limitations

* Workday mostly does not work from GitHub Actions. In testing, almost every Workday board answered HTTP 429 on the very first request. That looks like Workday blocking GitHub's server addresses, not a rate limit the bot can slow down for. Pacing, retries and splitting the list did not fix it. For these companies, use LinkedIn job alerts or the company's own email alerts.
* Sites that block datacenter traffic cannot be watched from here. Akamai and Cloudflare bot protection reject GitHub runners (HTTP 403, or a challenge page, or HTTP 202 with an almost empty body). Known cases: CBRE, Credit One Bank, UC Davis, Hertz, BNP Paribas.
* Sites that draw jobs with JavaScript cannot be read by the fallback scraper at all. The raw page has no job data. UBS (BrassRing) is an example. These need a different alert method.
* The fallback scraper guesses which links are jobs from their text and URL. It can miss postings or occasionally flag something that is not a job. Check the Actions log, which prints what it found.
* GitHub's scheduler is not dependable. Scheduled runs on free repos are sometimes delayed by many minutes or skipped with no error. If runs are missing, click Run workflow, or use an outside timer (for example cron-job.org) that starts the workflow every hour through GitHub's API. If you do that, use a fine-grained token limited to this repo with only Actions write permission, and keep it only inside that service.
* Keep the User-Agent current. iCIMS answers old User-Agents with a bare 405 Method Not Allowed. If several iCIMS companies start failing at once, bump the Chrome version in HEADERS first.
* Workday, Oracle, Avature and Phenom all use the same internal endpoints the career page itself calls. They are not documented public APIs, so they can change without warning. If a company that used to work goes quiet, check that first.
* Avature sites cap page size (25 per page works). Asking for more can return nothing or silently skip postings.
* Log order: error and warning lines appear before the normal lines in the Actions log, because they are written separately. Read the log by company name, not top to bottom.

Running it on your own computer

You can run it by hand any time:

pip install -r requirements.txt
python check_jobs.py

Without the Telegram secrets set, it prints what it would have sent. A computer that is asleep or off will not run anything on a schedule, so this is best for testing, not as the main home for the bot.

Files

* check_jobs.py: all the fetching, filtering and alert logic
* companies.json: the list of companies and their career URLs
* filters.json: location rules, role include and exclude words, search keywords
* seen_jobs.json: what has already been seen, updated automatically each run
* requirements.txt: Python packages
* .github/workflows/check-jobs.yml: the schedule and the run steps
