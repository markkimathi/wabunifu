# Kazi

A centralized board for **design jobs open to African designers**: product, UX, UI,
brand, motion. Aggregates from company career pages (via ATS APIs) and job boards,
tags each role with an honest **"can I apply from Kenya?"** badge, and links straight
to the original post. No candidate accounts. Kazi never hosts applications; every
role, scraped or employer-submitted, links out to the real apply page.

*("Kazi" = work/job in Swahili, a placeholder name, rename freely.)*

## How it works

```
company career pages ─┐
job boards ───────────┼─▶ scraper/run.py ─▶ web/jobs.json ─┐
                      ┘   (fetch → classify →              │
                           eligibility → dedupe)            ▼
employer "Post a job" ──▶ api/main.py ──▶ SQLite ──▶  GET /api/jobs ──▶ web/index.html
  (web/post.html)          (pending until    (api/          (merges both          (the board)
                            admin approves    kazi_          sources)
                            via admin.html)   submissions.db)
```

Two feeds merge into one board: the **scraper** (companies you add to
`sources/companies.py`) and **employer self-service submissions** (anyone can submit
via `web/post.html`, but nothing goes live until reviewed in `web/admin.html`). The
scraper side can still run standalone and static: `web/jobs.json` alone is enough to
serve the board with `python -m http.server`. The API is what adds the employer
journey and admin review on top; run it when you want that piece.

## Run it

**Scraper only (static, no server needed):**

```bash
cd scraper
pip install -r requirements.txt

python run.py --sample     # offline demo on fixtures → writes ../web/jobs.json
python run.py              # live fetch from the companies in sources/companies.py
```

```bash
cd web && python -m http.server 8000   # → http://localhost:8000, reads jobs.json
```

**With the employer "post a job" journey (adds a real backend + database):**

```bash
cd api
pip install -r requirements.txt

KAZI_ADMIN_TOKEN=choose-something-long uvicorn main:app --reload --port 8000
```

Open `http://localhost:8000`: the board, `/post.html` (employer submission form),
and `/admin.html` (review queue, needs `KAZI_ADMIN_TOKEN`) all come from this one
process. `GET /api/jobs` merges `web/jobs.json` with admin-approved submissions;
the frontend tries that endpoint first and falls back to the static file when the
API isn't running, so both modes work against the same `web/` folder.

## The pieces

| File | Does |
|---|---|
| `scraper/sources/greenhouse.py`, `lever.py`, `ashby.py`, `breezy.py` | Fetch listings from ATS public JSON APIs |
| `scraper/sources/companies.py` | **The seed list: edit this to add companies** (tokens verified live; see the file's own notes before trusting one) |
| `scraper/pipeline/classify.py` | Is it a design role? Which discipline + level? |
| `scraper/pipeline/eligibility.py` | The signature layer: kenya / africa / world / check |
| `scraper/pipeline/normalize.py` | Clean location, work type, salary |
| `scraper/pipeline/dedupe.py` | One role, even when mirrored across 5 boards |
| `scraper/run.py` | Orchestrates all of the above → `jobs.json` |
| `api/main.py` | FastAPI app: `POST /api/submissions`, `GET /api/jobs`, admin approve/reject, serves `web/` |
| `api/db.py` | SQLite storage for employer submissions (`pending` until an admin approves) |
| `web/index.html` | The board designers use |
| `web/post.html` | "Post a job": the employer submission form |
| `web/admin.html` | Review queue: approve/reject submissions (token-gated) |
| `.github/workflows/scrape.yml` | Refreshes the feed daily, commits the JSON |

## Extending it

- **More companies:** add entries to `sources/companies.py` (instructions in the file).
  Verify the token against the real API before adding it; a 404 on every run is
  worse than leaving a company out.
- **More ATS platforms:** Workday and SmartRecruiters are common too. Add a fetcher
  under `sources/` following `greenhouse.py`'s shape, plus a new `ats` value in
  `run.py`'s dispatch.
- **Kenyan boards (BrighterMonday, Fuzu):** deliberately **not** scraped.
  BrighterMonday's `robots.txt` disallows crawling job pages and search results, and
  Fuzu explicitly blocks a known job-aggregator bot sitewide. Worth asking either
  for direct API/feed access rather than scraping around those signals.
- **Better classification:** `classify.py` is rule-based on purpose. When rules get
  noisy, swap in an embedding classifier behind the same function signature.
- **Submission spam:** right now the only gate is manual admin review. If volume
  grows, add rate-limiting per IP/email in `api/main.py` before it becomes a problem.

## Deploy

- **Scraper + static board only:** point Vercel / Netlify / Cloudflare Pages at
  `web/`, and the GitHub Action refreshes `jobs.json` daily. No always-on
  infrastructure needed.
- **With the employer journey:** the API needs a host that keeps a process (and
  `api/kazi_submissions.db`) running. Render, Railway, Fly.io, or a small VPS all
  work. Set `KAZI_ADMIN_TOKEN` to a real secret in that environment; the code
  falls back to a dev-only default if it's unset, which is fine locally and unsafe
  anywhere public.

## Legal note

Prefer ATS APIs and boards whose terms permit aggregation. LinkedIn and Indeed
prohibit scraping, so don't. Linking out to a source you're allowed to read is the
model here.
