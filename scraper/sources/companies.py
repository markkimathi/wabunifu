"""
The seed list Kazi pulls from. Every entry below has had its (ats, token) pair
verified live against the real public API as of 2026-07-28 — each returns a
real board, not a 404. Verification command, if you want to re-check one:

  greenhouse:  curl -s https://boards-api.greenhouse.io/v1/boards/<token>/jobs
  lever:       curl -s https://api.lever.co/v0/postings/<token>?mode=json
  ashby:       curl -s https://api.ashbyhq.com/posting-api/job-board/<token>
  breezy:      curl -s https://<token>.breezy.hr/json

A 404 means the token/board is wrong or the company moved ATS — companies
rename boards and migrate providers often, so re-verify periodically rather
than trusting this list forever.

How to add a company:
  1. Open their careers page and find the "Apply"/board link.
  2. Match the domain to an ats above and verify the token with the curl
     command for that provider before adding it — a 200 with a `jobs` (or
     bare list, for breezy) key is a real board.
  3. Workday and SmartRecruiters are common too but need their own fetcher
     under sources/ (different response shape) — add one when you hit a
     company that needs it.
"""

COMPANIES = [
    # --- Pan-African / fintech ---
    {"name": "Moniepoint",         "ats": "greenhouse", "token": "moniepoint"},

    # --- East Africa / Kenya ---
    {"name": "Apollo Agriculture", "ats": "lever",      "token": "apolloagriculture"},
    {"name": "Wasoko",             "ats": "breezy",     "token": "wasoko"},

    # --- Global, hire remote (eligibility layer decides who can apply) ---
    {"name": "GitLab",             "ats": "greenhouse", "token": "gitlab"},
    {"name": "Andela",             "ats": "ashby",      "token": "andela"},
    {"name": "Deel",               "ats": "ashby",      "token": "deel"},
    {"name": "Canonical",          "ats": "greenhouse", "token": "canonical"},

    # --- Known to hire designers, ATS/token NOT YET VERIFIED ---
    # Their public careers pages render job listings client-side (React/Vue),
    # so the API call isn't visible in the page source — confirming the real
    # endpoint needs a browser network trace, not just curl. Don't re-add
    # these with a guessed token; a 404 on every run is worse than absence.
    # Flutterwave  -> careers.flutterwave.com (custom board, ATS unconfirmed)
    # Paystack     -> was on Greenhouse ("paystack"), board now returns 404 —
    #                 likely renamed/migrated post Stripe integration
    # Chipper Cash -> chippercash.com/careers (ATS unconfirmed)
    # Yoco         -> careers.yoco.com (ATS unconfirmed)
    # M-KOPA       -> ATS unconfirmed
    # Sun King     -> sunking.pinpointhq.com (Pinpoint ATS — needs a new fetcher)
]
