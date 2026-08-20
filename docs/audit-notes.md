# Audit notes — Path & Pixel

Companion to `mobbin-research.md`. That file records what was learned from
looking at other products; this one records what was learned from auditing
this one. Same reason for existing: conversations get compacted, and a lesson
that only lives in a chat log has to be re-derived from scratch.

---

## The failure this codebase keeps producing

**Features are built end-to-end except the surface a person touches.**

Every one of these was found by asking "who sees this, and where?" rather than
by reading code for correctness. The code was correct in all of them.

| Feature | Built | Missing |
|---|---|---|
| Email verification | tokens, expiry, send | no code entry — nobody could ever verify, so job alerts had never reached a single designer |
| Experience / role history | full CRUD | no editor |
| Company logo upload | endpoint, storage | no upload control |
| Employer notifications | written on every event | bell resolved designers only, so employers saw none |
| Cross-border note | required from employers | rendered nowhere |
| Follows | table, toggle, notify-on-new-work | no count on any designer profile, no notification to the person followed |
| Community Q&A | questions, replies, votes | told nobody anything |
| Application stages | employer board, designer tab | moving someone to Interviewing was silent |

The tell is always the same: the data model and the API are complete and
symmetrical, and exactly one endpoint or one element — the human end — is
absent. Reading the backend will never surface it. Walking the flow as the
person on the receiving end always does.

**When auditing, pick a feature and ask: who is told, on what screen, and what
do they do next?** If any of those three has no answer, that is the bug.

## Second recurring shape: state that only the list endpoint checks

Community sessions keep `status = "scheduled"` forever — nothing sweeps them
when their date passes. The list endpoints compared against today; the book
endpoint and the detail page did not. So a past session still sold seats and
still handed out its joining link.

Jobs got this right (`_applications_open` and `_combined_jobs` both compare
against `date.today()`), which is what made the session version findable —
the same question, asked of a different feature.

**If a record expires by date rather than by a status change, every endpoint
that reads it needs the date check, not just the one that lists it.**

## Scope discipline — what was deliberately not built

Production, as of 20 August 2026: 5 designers, 2 companies, 0 applications,
0 listings taking applications, 0 follows, 0 conversations, 3 sessions,
0 bookings.

At that size, some correct-sounding features are speculative:

- **Company follows** — not wired. No registered companies to follow.
- **Session reminders** — not built. 3 sessions, 0 bookings.
- **A rejection / "not moving forward" stage** — not built. A whole state
  machine for zero applications. The proportionate fix was making the existing
  Remove dialog say what it actually destroys.
- **A "0 followers" line** — the count renders only above zero. At this size a
  zero under every profile reads as an empty room, and it is a number nobody
  can act on.

This is the Mobbin caveat applied to our own backlog: *Contra's earnings strip
reads as confident there and would read as empty here.* Check the production
counts before building the surface — `flyctl ssh console -a wabunifu` and query
`/data/kazi_submissions.db`.

## Verified, and not

**Verified:** every notification href resolves and opens the tab it names
(`?tab=` is honoured by both dashboards); eligibility verdicts agree across the
board, job page, homepage and company page; application flow end to end
including stage moves; past-session handling.

**Never audited:** real devices, any browser other than the WebKit preview
pane, actual email delivery, screen readers, performance under load.
