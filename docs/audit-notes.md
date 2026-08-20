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
| Designer review | approve/reject endpoints, status column | neither outcome was ever sent — people waited indefinitely for a decision already made |
| Company review | same | same |
| Suspension | rule + reason recorded | shown only to admins; the person was told nothing, and `/me` 403'd so the banner explaining it could never render — the dashboard hung on "Loading…" forever |

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

## Third shape: two routes into one state, one of them forgotten

A designer reaches `suspended` two ways — the admin suspend dialog, and
resolving a report with `action="suspend"`. Making suspension explain itself
covered the dialog; the report path still called `set_designer_status()`
directly and recorded nothing. Both now go through one helper.

This is the Mobbin lesson in miniature: *a finding is not done when the surface
it was found on is fixed.* **When a fix adds a side effect to a state change,
grep for every writer of that state, not just the one you were looking at.**

A related self-inflicted version: I concluded employer suspension was
unreachable after grepping `suspend_employer`. The function is
`set_employer_suspended`. Grep for the column name, not the verb you expect.

## Fourth shape: the fix that was applied one selector at a time

`el.hidden = true` does nothing to an element whose class sets `display` — any
author rule outranks the UA stylesheet's `[hidden]`. This codebase hit that
four times and patched it per-selector each time, twice leaving a comment
warning whoever hit it next. Whoever hit it next was two lines below the
comment.

The visible consequences: an "Applied" tab shown to signed-out visitors, a
"Save this search" button offered on an unfiltered board its own comment says
there is nothing to save on, a "Saved searches" heading over an empty list, and
two "0" badges that wouldn't go away. Now one rule in `pp-primitives.css`:
`[hidden]{display:none !important}`.

**When the same bug recurs on a new selector, the fix belongs in the shared
layer, not on the new selector.**

Worth reusing: the way this was found. Rather than reading for it, run this in
the console on each page and at each width —

```js
[...document.querySelectorAll('[hidden]')]
  .filter(e => getComputedStyle(e).display !== 'none')
```

— plus `document.documentElement.scrollWidth > innerWidth` for horizontal
overflow. Both are true/false answers, not judgement calls.

## Fifth shape: correct only by virtue of where it happens to be routed

Sixteen pages linked `tokens.css` relatively. For a page served at a top-level
route that is identical to absolute, so fifteen of them worked. `pp-invite.html`
is served at `/invite/{token}`, so its links resolved to `/invite/tokens.css` —
which the catch-all answers with **HTML, status 200**, not a 404. The browser
refuses a stylesheet served as `text/html`, silently, so every colleague ever
invited to a team saw an unstyled page in Times New Roman.

Two things worth carrying:

- A catch-all route turns "missing asset" from a 404 into a 200 of the wrong
  type. Check `content_type`, not just status, when an asset seems absent.
- **Fifteen of those pages were not correct, they were lucky.** When a fix
  applies to one instance of a pattern, apply it to the pattern.

## Environment caveat: the preview pane is a hidden tab

`document.hidden` is `true` in the Browser pane, so **CSS transitions do not
progress and `requestAnimationFrame` never fires**. A `getComputedStyle`
transform read after opening a sheet returns the *start* of the animation
forever, which reads exactly like a broken sheet — the mobile filter sheet
appeared to open one full viewport off-screen and was in fact fine.

To measure a real resting position, disable the transition first:

```js
el.style.transition = 'none';
el.classList.add('-open');
void el.offsetHeight;          // force reflow
el.getBoundingClientRect();    // now trustworthy
```

Screenshots force a paint and are reliable; computed transforms mid-animation
are not.

## Empty states: three questions that catch the bad ones

Swept every list-rendering surface by loading the site with accounts that had
nothing. The panels that were wrong were wrong in one of three ways:

1. **Does it name an action without offering it?** The employer's Listings
   panel said "post your first role" and the Applicants one said "add one from
   the Listings tab" — neither had a button.
2. **Is it your own doing?** "Nothing saved yet" reads as your own doing.
   Signed out it isn't, because the list lives on the account. Different
   situation, different copy.
3. **Whose profile is this?** Your own empty profile said "Blank Person hasn't
   added any featured work" — about you, in the third person. That one had a
   cause worth remembering: `renderProfile()` paints the tab before
   `loadViewerState()` resolves who's looking, so `isOwn` was always false at
   paint time. **An "is this me" branch is worthless if it runs before the
   answer is known.**

The check that found them, run per page with an account that has nothing:

```js
const p = document.querySelector('.pp-empty,.state-panel');
({ panel: !!p, icon: !!p?.querySelector('svg'), action: !!p?.querySelector('button,a') })
```

No icon is a polish gap; no action next to copy that names one is a bug.

## Sixth shape: content that was never true

The largest single category found, and the one no amount of code reading
surfaces — it lives in production rows and in static copy, not in logic.

What was on the live site:

- **Three community sessions with invented hosts** ("Amina Kiptoo"), invented
  credentials ("has reviewed 500+ resumes") and Google Meet links that were not
  valid meeting codes. Bookable. The dead link was revealed only *after*
  booking a seat for a review that did not exist.
- **A "Mentors" card on the homepage** describing designers who keep slots open
  each month, with a link to see who's available. The word appeared nowhere
  else in the codebase — no endpoint, no table, no page.
- **An invented testimonial** in quotation marks on the sign-in page,
  attributed to "a designer using Path & Pixel", when two designers had
  profiles and nobody had applied to anything.
- **Claims the schema cannot support**: "designers who published something this
  month" and "case studies published here this month" — `designer_projects` has
  no `created_at` column at all. "The projects worth stopping on this month" —
  that query has no time window.
- **Claims that expired**: "Applications always go straight to the company's
  own page" stopped being true the day employers could take applications here.
  It was in three places.
- **Deploy-check debris**: accounts and companies on RFC-reserved domains, and
  a listing titled `__CUTOVER_VERIFY_DELETE_ME__` sitting in the live review
  queue.

Three questions that find this class:

1. **Does a name in the data belong to a real person?** Invented hosts and
   testimonials are the worst kind, because they're the ones a user will act on.
2. **Can the schema support this sentence?** "This month" needs a timestamp
   column. Grep the query behind the claim before trusting the copy.
3. **Does this feature exist?** Grep the noun. If "mentor" appears only in the
   page advertising it, it is not a feature.

**How it's prevented now:** validators refuse unreachable content at the door
(reserved domains on listing URLs, company websites and joining links; Meet
links must carry a real `abc-defg-hij` code), and `purge_placeholder_content()`
sweeps at startup for anything unreachable by construction. Both share one list
of reserved domains, in `db.py`, because two copies is how they drift.

**And the reason it accumulated:** rejecting a listing keeps it, and cancelling
a session keeps it, and neither had a delete. There was no way to remove a
submission or a session at all, so every mistake stayed for good. Both now
have one. **A moderation queue with no exit fills up with things nobody meant
to keep.**

## Seventh shape: a default that only holds because nobody made a mistake

`ADMIN_TOKEN` fell back to `"dev-only-change-me"` — a string published in
`api/main.py` — whenever `KAZI_ADMIN_TOKEN` was unset. The comment above it
said to set a real one before deploying anywhere reachable, and that comment
*was* the entire control. Production happened to have the secret set, so it
was never wrong; it was one forgotten `flyctl secrets set` away from shipping
an open admin console.

The fix is to make the mistake fail closed: on a hosted instance (`FLY_APP_NAME`
is set by the Fly runtime and absent locally) the dev default is refused with a
503 that names the cause. Local development still works with no env var.

**A comment telling the next person to do the right thing is documentation, not
a control.** When an insecure default exists for developer convenience, make
the production path refuse it rather than trusting deploys to override it.

Audited every other `os.environ.get` while there: `RESEND_API_KEY` defaults to
empty (no key means no send, logged — fails closed), and the rest are paths and
public URLs. This was the only one.

Also worth knowing about the admin token: it is a single shared secret used as
both the console login and the CI credential for the digest workflow, so
rotating it means updating Fly and the GitHub secret together. It goes in an
`Authorization` header and never a URL, so it stays out of server logs and
browser history, and `require_admin` now compares with `secrets.compare_digest`.

## Accessibility: what a first pass actually found

Most of it was already right, which is worth recording so the next pass starts
from the gaps rather than from scratch: **no control anywhere lacks an
accessible name**, every image carries an `alt`, every page has exactly one
`h1`, and modals already trap focus (`PPSheet`). Three things were not.

- **Search inputs had no name.** All five — homepage hero, jobs board, people
  directory, and both nav overlays — leaned on the browser falling back to the
  placeholder. The board's input is wrapped in a `<label>`, but that label
  contains only a magnifying-glass icon, so it named nothing. A wrapping label
  is not automatically an accessible name.
- **Heading levels skipped.** Job titles were `h4` directly under the page
  `h1`; homepage cards were `h4` under section `h2`s. Skimming by heading is
  how a screen-reader user reads a job board, and every item jumped two levels.
  Job titles and person names are now `h2`, homepage cards `h3`. The CSS keyed
  on the tag in six places, so each selector moved with its element and the
  computed styles are byte-identical.
- **One contrast failure sitewide**, and only in dark: white-on-gold at 1.57:1
  on the join banner. Its siblings pin fixed colours because the banner is gold
  in both themes; the button kept a theme token that flips.

Two things measured and correctly left alone: `"Previous"` and `"Check my CV"`
fail contrast only while `disabled`, which WCAG 1.4.3 exempts.

The checks, all runnable in the console — they answer true/false, not taste:

```js
// controls with no accessible name
[...document.querySelectorAll('button,a[href]')]
  .filter(e => !(e.getAttribute('aria-label') || e.textContent).trim())
// heading level jumps
// contrast: compute luminance of getComputedStyle(el).color against the
// nearest ancestor with a non-transparent backgroundColor
```

**Measuring theme-dependent styles:** set the theme the way the app does
(`localStorage.pp_theme`) and reload. Flipping `data-pp-theme` at runtime and
reading `getComputedStyle` immediately returns stale values — it reported a
button as black-on-black that was in fact black-on-white. Same family as the
frozen-transition trap above: **the preview pane lies about anything that
depends on a recompute you did not wait for.**

## Scope discipline — what was deliberately not built

Production, as of 20 August 2026: 5 designers, 2 companies, 0 applications,
0 listings taking applications, 0 follows, 0 conversations, 3 sessions,
1 booking (cancelled), 0 pay submissions, 0 questions, 0 saved searches,
3 projects, 0 notifications.

At that size, some correct-sounding features are speculative:

- **Company follows** — not wired. No registered companies to follow.
- **Session reminders** — not built. 3 sessions, 0 bookings.
- **A rejection / "not moving forward" stage** — not built. A whole state
  machine for zero applications. The proportionate fix was making the existing
  Remove dialog say what it actually destroys.
- **A "0 followers" line** — the count renders only above zero. At this size a
  zero under every profile reads as an empty room, and it is a number nobody
  can act on.
- **Telling a pay-data contributor their figure was accepted** — 0 pay
  submissions exist. The cheap correctness half was worth doing (both admin
  decisions reported success for ids that don't exist); the notification half
  was not.

## Live data that needs a person, not a commit

The three community sessions carry joining links of the form
`https://meet.google.com/kazi-portfolio-sept`. That is not a valid Google Meet
code — real ones are `xxx-xxxx-xxx`. The link is only revealed once someone
books, so the first person to book any of the three gets sent somewhere that
doesn't exist. This is production data an admin typed, not a code path, so it
needs real meetings created and the links pasted in.

This is the Mobbin caveat applied to our own backlog: *Contra's earnings strip
reads as confident there and would read as empty here.* Check the production
counts before building the surface — `flyctl ssh console -a wabunifu` and query
`/data/kazi_submissions.db`.

## Verified, and not

**Verified:** every notification href resolves and opens the tab it names
(`?tab=` is honoured by both dashboards); eligibility verdicts agree across the
board, job page, homepage and company page; application flow end to end
including stage moves; past-session handling; designer and company review
(approve, repeat-approve, reject with reason) end to end including the admin
dialog; suspension from both routes; the suspended dashboard.

Emails are verified as *sent* — the local server logs each one it would post to
Resend. Actual delivery has never been checked from here.

**Never audited:** real devices, any browser other than the WebKit preview
pane, actual email delivery, screen readers, performance under load.

## One more environment caveat: buffered stdout

Run the local server with `python3 -u`. Without it, `print()` from `api/email.py`
is block-buffered while uvicorn's own logging is not, so the email lines appear
out of order or not at all. This looked exactly like "forgot-password returns ok
and sends nothing" — a critical bug that wasn't there. The reset flow was fine.
