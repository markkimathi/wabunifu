"""
The seed list Kazi pulls from. Every entry below has had its (ats, token) pair
verified live against the real public API as of 2026-07-31: each returns a
real board, not a 404. Verification command, if you want to re-check one:

  greenhouse:  curl -s https://boards-api.greenhouse.io/v1/boards/<token>/jobs
  lever:       curl -s https://api.lever.co/v0/postings/<token>?mode=json
  ashby:       curl -s https://api.ashbyhq.com/posting-api/job-board/<token>
  breezy:      curl -s https://<token>.breezy.hr/json
  hibob:       curl -s https://<token>.careers.hibob.com/api/job-ad -H "Referer: https://<token>.careers.hibob.com/"
  pinpoint:    curl -s https://<token>.pinpointhq.com/jobs.rss
  chippercash: company-specific HTML scraper, no token needed; see sources/chippercash.py

A 404 means the token/board is wrong or the company moved ATS. Companies
rename boards and migrate providers often, so re-verify periodically rather
than trusting this list forever.

How to add a company:
  1. Open their careers page and find the "Apply"/board link.
  2. Match the domain to an ats above and verify the token with the curl
     command for that provider before adding it: a 200 with a `jobs` (or
     bare list, for breezy) key is a real board.
  3. If the site renders job data client-side with no visible API call (a
     Nuxt/Next SPA embedding state in the page rather than fetching it),
     that needs real headless-browser rendering to scrape, which is out of
     scope for this requests-only pipeline. Flutterwave and Paystack are
     like this; see the note below rather than guessing a fetcher for them.
  4. Workday and SmartRecruiters are common too but need their own fetcher
     under sources/ (different response shape); add one when you hit a
     company that needs it.
"""

COMPANIES = [
    # --- Pan-African / fintech ---
    {"name": "Moniepoint",         "ats": "greenhouse",   "token": "moniepoint"},
    {"name": "Yoco",               "ats": "hibob",        "token": "yoco"},
    {"name": "Chipper Cash",       "ats": "chippercash",  "token": ""},

    # --- East Africa / Kenya ---
    {"name": "Apollo Agriculture", "ats": "lever",         "token": "apolloagriculture"},
    {"name": "Wasoko",             "ats": "breezy",        "token": "wasoko"},
    {"name": "M-KOPA",             "ats": "ashby",         "token": "M-KOPA"},
    {"name": "Sun King",           "ats": "pinpoint",      "token": "sunking"},

    # --- Global, hire remote (eligibility layer decides who can apply) ---
    {"name": "GitLab",             "ats": "greenhouse", "token": "gitlab"},
    {"name": "Andela",             "ats": "ashby",      "token": "andela"},
    {"name": "Deel",               "ats": "ashby",      "token": "deel"},
    {"name": "Canonical",          "ats": "greenhouse", "token": "canonical"},
    {"name": "Coinbase",           "ats": "greenhouse", "token": "coinbase"},
    {"name": "Asana",              "ats": "greenhouse", "token": "asana"},
    {"name": "Airtable",           "ats": "greenhouse", "token": "airtable"},
    {"name": "Dropbox",            "ats": "greenhouse", "token": "dropbox"},
    {"name": "Intercom",           "ats": "greenhouse", "token": "intercom"},
    {"name": "Twilio",             "ats": "greenhouse", "token": "twilio"},
    {"name": "Okta",               "ats": "greenhouse", "token": "okta"},
    {"name": "Databricks",         "ats": "greenhouse", "token": "databricks"},
    {"name": "Robinhood",          "ats": "greenhouse", "token": "robinhood"},
    {"name": "Brex",               "ats": "greenhouse", "token": "brex"},
    {"name": "Figma",              "ats": "greenhouse", "token": "figma"},
    {"name": "Webflow",            "ats": "greenhouse", "token": "webflow"},
    {"name": "Reddit",             "ats": "greenhouse", "token": "reddit"},
    {"name": "Cloudflare",         "ats": "greenhouse", "token": "cloudflare"},
    {"name": "Mozilla",            "ats": "greenhouse", "token": "mozilla"},
    {"name": "Palantir",           "ats": "lever",      "token": "palantir"},
    {"name": "Ramp",               "ats": "ashby",      "token": "ramp"},
    {"name": "Notion",             "ats": "ashby",      "token": "notion"},
    {"name": "Linear",             "ats": "ashby",      "token": "linear"},

    # --- African companies, verified board, no design roles as of last
    #     check — kept anyway since new roles are exactly what a daily
    #     scrape is for; harmless if a run finds zero. ---
    {"name": "Jumia",              "ats": "greenhouse", "token": "jumia"},
    {"name": "LemFi",              "ats": "ashby",      "token": "lemfi"},

    # --- Known to hire designers, ATS/token NOT YET VERIFIED ---
    # Their public careers pages render job listings client-side (a Nuxt SPA
    # embedding state in the page via a non-JSON serialized blob), so the
    # data isn't reachable with requests-only fetching; would need a real
    # headless browser (Playwright) to render and extract, which is a much
    # heavier dependency than anything else in this pipeline. Left out
    # rather than half-implemented.
    # Flutterwave  -> flutterwave.com/us/careers/vacancies (Nuxt 2, Contentful
    #                 CMS behind it, state serialized as executable JS not JSON)
    # Paystack     -> was on Greenhouse ("paystack"), board now returns 404,
    #                 likely renamed/migrated post Stripe integration
]
