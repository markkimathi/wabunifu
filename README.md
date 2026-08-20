# Kazi

A platform for **African/Kenyan designers**: a jobs board (product, UX, UI, brand,
motion — aggregated from company career pages and job boards, each role tagged with
an honest **"can I apply from Kenya?"** badge and linking straight to the original
post) plus **designer profiles** (accounts, portfolios with featured projects,
résumé/ATS-check tooling, and a public directory employers can browse). Kazi never
hosts job applications — every role, scraped or employer-submitted, links out to the
real apply page.

*("Kazi" = work/job in Swahili, a placeholder name, rename freely.)*

**Live:** [kazi.odana.design](https://kazi.odana.design) (Fly.io app `wabunifu`,
also reachable at `wabunifu.fly.dev`).

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

designer signup/login ──▶ api/main.py ──▶ SQLite ──▶ GET /api/designers ──▶ web/designers.html
  (web/onboarding.html,     (auth, profile CRUD,   (same DB,      (directory)         + web/designer.html
   web/account.html)        projects, résumé,       new tables)                        (public profile)
                             admin approval
                             via admin.html)
```

Two feeds merge into one jobs board: the **scraper** (companies you add to
`sources/companies.py`) and **employer self-service submissions** (anyone can submit
via `web/post.html`, but nothing goes live until reviewed in `web/admin.html`). The
scraper side can still run standalone and static: `web/jobs.json` alone is enough to
serve the board with `python -m http.server`. The API is what adds the employer
journey, designer accounts, and admin review on top; run it when you want those
pieces.

Designer accounts are a separate feature sharing the same API/DB: signup → email
verification → onboarding wizard (bio, links, skills, contact, photo, featured
projects, résumé) → **pending** until an admin approves in `admin.html` → visible in
the public directory (`designers.html`) and on their own profile page
(`designer.html`).

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

**Full app (employer journey + designer accounts, needs a real backend + database):**

```bash
cd api
pip install -r requirements.txt

KAZI_ADMIN_TOKEN=choose-something-long uvicorn main:app --reload --port 8000
```

Open `http://localhost:8000`: the board, `/post.html` (employer submission form),
`/account.html` / `/onboarding.html` (designer signup + dashboard), `/designers.html`
(public directory), and `/admin.html` (review queue for both submissions and
designer profiles, needs `KAZI_ADMIN_TOKEN`) all come from this one process.
`GET /api/jobs` merges `web/jobs.json` with admin-approved submissions; the frontend
tries that endpoint first and falls back to the static file when the API isn't
running, so both modes work against the same `web/` folder.

Optional env vars for local dev: `KAZI_DB_PATH` (defaults to
`api/kazi_submissions.db`), `RESEND_API_KEY` (verification/reset emails — without it,
`api/email.py` just logs instead of sending).

## The pieces

| File | Does |
|---|---|
| `scraper/sources/greenhouse.py`, `lever.py`, `ashby.py`, `breezy.py`, `hibob.py`, `pinpoint.py`, `chippercash.py` | Fetch listings from ATS public JSON APIs |
| `scraper/sources/companies.py` | **The seed list: edit this to add companies** (tokens verified live; see the file's own notes before trusting one) |
| `scraper/pipeline/classify.py` | Is it a design role? Which discipline + level? |
| `scraper/pipeline/eligibility.py` | The signature layer: kenya / africa / world / check |
| `scraper/pipeline/normalize.py` | Clean location, work type, salary |
| `scraper/pipeline/dedupe.py` | One role, even when mirrored across 5 boards |
| `scraper/desc_format.py` | Sanitizes/normalizes job description HTML for both scraped and employer-submitted roles |
| `scraper/run.py` | Orchestrates all of the above → `jobs.json` |
| `api/main.py` | FastAPI app: jobs/submissions/admin routes, full designer auth + profile + projects + résumé CRUD, serves `web/` |
| `api/db.py` | SQLite storage — job submissions, designers, designer links/projects/résumé, all admin-gated where relevant |
| `api/email.py` | Resend wrapper for verification codes + password reset emails |
| `api/photo.py` | Validates/crops designer profile photos (square avatar crop) |
| `api/project_image.py` | Validates/crops featured-project cover images (4:3 landscape crop) |
| `api/geoip.py` | IP → country lookup (`geoip_data/`), used for the "can I apply from Kenya" signal on analytics |
| `api/ats_check.py` | Résumé-vs-job-description keyword match for the "Check Against Your Resume" flow |
| `web/index.html` | The jobs board |
| `web/job.html` | Single job detail page |
| `web/post.html` | "Post a job": the employer submission form |
| `web/admin.html` | Review queue: approve/reject job submissions **and** designer profiles (token-gated) |
| `web/account.html` | Designer dashboard: Home / Edit Profile / Featured Projects tabs, plus signup/login/verify gates |
| `web/onboarding.html` | Multi-step signup wizard (bio → links → skills → contact → photo → featured projects → résumé) |
| `web/designers.html` | Public designer directory grid |
| `web/designer.html` | A single designer's public profile |
| `web/cv-check.html` | Standalone "check your résumé against a JD" tool |
| `web/login.html`, `web/signup.html` | Designer auth entry points |
| `web/featured-projects.js` | Shared component (self-injecting script): renders project cards in 3 variants (`manage`/`compact`/`public`), the add/edit modal form, and delete/reorder calls. Loaded by every page that shows featured projects — see **Front-end conventions** below before editing it |
| `web/nav.js`, `web/theme.js`, `web/confirm-dialog.js`, `web/password-toggle.js` | Other shared self-injecting scripts (nav bar, light/dark theme, confirm-dialog modal, password show/hide) |
| `.github/workflows/scrape.yml` | Refreshes the feed daily **and redeploys to Fly** — see **Gotcha** below |

## Front-end conventions (read before editing `web/`)

- **No bundler.** Every route in `web/` is a standalone static HTML file with its own
  inline `<style>`/`<script>`, duplicating markup and helpers on purpose. The only
  shared code lives in a handful of self-injecting scripts loaded via `<script>` tags
  in `<head>`: `nav.js`, `theme.js`, `confirm-dialog.js`, `password-toggle.js`,
  `featured-projects.js`. Each is an IIFE that guards against double-init, defers its
  DOM injection to a `ready()` call (since it loads before `<body>` exists), and
  injects its own `<style>` block reading the page's existing CSS custom properties.
  Follow this pattern for any new shared behavior — don't introduce a bundler or a
  new sharing mechanism for a one-off.
- **Cache-busting convention:** every shared script tag carries a manual version
  query string — `theme.js?v=6`, `nav.js?v=10`, `featured-projects.js?v=2`, etc.
  **Bump the version number every time you edit one of these files' content and
  update every `<script src="...">` reference to it across all pages that load it**,
  otherwise a browser with an old cached copy of the file keeps serving stale code
  indefinitely after deploy (server sets no explicit `Cache-Control` header, so this
  is the only real invalidation mechanism). Grep the filename across `web/*.html` to
  find every reference before bumping.
- **CSS specificity is used deliberately** in a couple of places (e.g.
  `featured-projects.js`'s `.fp-grid.-compact` overriding viewport media queries on
  plain `.fp-grid`) to force one variant of a shared component to behave differently
  from the default regardless of screen width. If a responsive rule doesn't seem to
  be applying, check for a more-specific selector doing this intentionally before
  assuming it's a bug.

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

The live app runs on **Fly.io** (app name `wabunifu`, region `jnb` — Johannesburg,
closest to East Africa), config in `fly.toml`. The Dockerfile does
`COPY api/ web/ scraper/` from the **local working directory**, not from git — so
`flyctl deploy` picks up whatever is on disk regardless of commit state.

```bash
flyctl deploy -a wabunifu
```

(`flyctl` may not be on `PATH` by default — check `~/.fly/bin/flyctl` if the command
isn't found.) `KAZI_DB_PATH` is set in `fly.toml` to a path on a mounted volume
(`kazi_data`) so the SQLite DB survives redeploys. Set `KAZI_ADMIN_TOKEN` as a real
Fly secret (`flyctl secrets set KAZI_ADMIN_TOKEN=...`) — the code falls back to a
dev-only default if it's unset, which is fine locally and unsafe in production.

**⚠️ Gotcha — push to `main` promptly after every deploy.**
`.github/workflows/scrape.yml` runs daily (05:00 UTC) and, whenever it updates
`jobs.json`, **also runs `flyctl deploy --remote-only -a wabunifu` itself** — from
its own GitHub-checked-out copy of `main`, not from any local machine. If you deploy
a fix from local edits that aren't pushed to `main` yet, the next cron run will
silently redeploy stale `main` and revert your fix in production with no error or
conflict to signal it happened. **Commit and push right after verifying a deploy
live**, don't batch it for later.

- **Static board only (no employer/designer features):** point Vercel / Netlify /
  Cloudflare Pages at `web/`; the GitHub Action refreshes `jobs.json` daily. No
  always-on infrastructure needed — but you lose `flyctl deploy` step above along
  with it, so update the workflow if you go this route.
- **Full app:** needs a host that keeps a process (and the SQLite DB) running —
  what production actually uses today is Fly.io as described above.

## Working on this with Claude Code

If you're an AI agent picking this project up fresh:

- **Design research lives in `docs/`, not in the conversation.** `docs/mobbin-research.md`
  holds the Mobbin study this product's roadmap came from — what was reviewed, the seven
  moves it produced, which shipped, and the caveats. Read it before starting design work,
  and write any new round into it. It exists because that research was once done in a chat,
  compacted away, and then confidently reported as never having happened. Conversation
  context is not storage.

- **Development happens directly against the live site**, not local-first: edit →
  `flyctl deploy -a wabunifu` → verify against `https://kazi.odana.design/` (or
  `wabunifu.fly.dev`) → commit → push. Local `uvicorn`/`http.server` runs are still
  useful for quick syntax/logic checks before deploying, but don't treat them as the
  primary verification step — always confirm the real fix against the live URL.
  Deploying and pushing to git are two separate steps; do both, but don't assume one
  implies the other unless asked.
- **Git identity for this repo** is set locally (not global):
  `Mark Kimathi <maqadamz600@gmail.com>`. Remote is
  `https://github.com/markkimathi/wabunifu.git`, branch `main`.
  `git fetch`/merge before pushing — the `scrape.yml` bot (`kazi-bot`) commits to
  `main` daily and can cause a rejected push if you're behind.
  See the **Gotcha** above in Deploy: push promptly after every verified deploy.
  Local dev admin token default: `dev-only-change-me` (set `KAZI_ADMIN_TOKEN` to
  something real anywhere public).
- **Before editing anything shared** (`web/nav.js`, `theme.js`,
  `confirm-dialog.js`, `password-toggle.js`, `featured-projects.js`), read
  **Front-end conventions** above — especially the cache-busting version-bump rule,
  which has caused a live "my change isn't showing up" report before.
- **No bundler, per-page duplication is intentional** — don't refactor shared markup
  out of individual `web/*.html` files into a new shared mechanism unless asked; the
  existing shared-script pattern above is the one exception already established.

## Legal note

Prefer ATS APIs and boards whose terms permit aggregation. LinkedIn and Indeed
prohibit scraping, so don't. Linking out to a source you're allowed to read is the
model here.
