# Mobbin research — Path & Pixel

**Why this file exists.** This research was done on 19 August 2026 inside a Claude
Code conversation. That conversation was later compacted, the findings fell out of
context, and I subsequently told Mark — twice, flatly — that no Mobbin research
existed for this project. It did. I then re-ran part of it and presented the same
flows back as new.

Conversation context is not durable. This file is. Anything worth keeping from a
research session belongs here, not in a chat log.

---

## What was reviewed

32 web screens on Mobbin, across three categories chosen to match what Path &
Pixel actually is: **talent directories**, **job boards**, and **creator
dashboards**.

Apps surfaced and reviewed, by how often they came back:

| App | Why it was relevant |
|---|---|
| Wellfound | Closest analogue: job board with eligibility/visa constraints |
| Behance, Dribbble | Designer profiles and portfolio surfaces |
| Contra, Peerlist, Braintrust, Upwork | Talent directories and profile completeness |
| Deputy, Mercor | Application flows end to end |
| Remote | Cross-border employment framing |
| Kajabi, Podia, Substack, Patreon | Creator dashboards |
| Airbnb | Searched separately, earlier, for a different question |

Every claim was checked against this codebase rather than from memory. That
verification step is the reason the findings were actionable, and it is worth
repeating in any future round.

---

## The seven moves

Full write-up, with the evidence → verification → move structure and a table
ranking all seven by payoff against build cost:

**<https://claude.ai/code/artifact/383e9f16-b043-4be8-b95a-81c6e3b3ef77>**
("Seven Moves for Path & Pixel")

A second artifact covers the designer↔recruiter gap:
**<https://claude.ai/code/artifact/30539680-1934-490c-b913-8c4929ff1bbe>**
("The Severed Loop")

### 01 — Eligibility is a label, not a filter · SHIPPED

The whole promise is reading every posting and saying whether it is open to
someone *where they live*. The reading happened; the board never asked where they
live and never used it. `pp-jobs.html` mentioned "location" only as a job's own
city and as `location.search`. Designers stored a location, but as free text that
four pages read purely to display.

Wellfound makes a weaker version of this claim and still does the harder half:
location lives on the account, and a line above the results says plainly what is
being hidden and how to undo it.

*Shipped:* country on the account, an anonymous country picker on the board that
needs no signup, and the "Hiding N roles that aren't open from X" line with its
undo in the same sentence.

### 02 — Profile strength exists, but only during onboarding · SHIPPED

The meter worked and then vanished the moment onboarding completed. The people
most likely to have a thin profile are exactly the ones who skipped ahead and
never saw it again.

Contra's framing is the one worth stealing: not "60% complete" but "complete this
and you'll start appearing in search results" — the consequence, not the
percentage.

*Shipped:* the strength panel on the dashboard overview, with each item saying
what it unlocks.

### 03 — Saved searches · SHIPPED

Filters were session-only, so someone hunting junior brand roles rebuilt that
query every visit. The Friday newsletter already shipped to everyone; sending each
designer the roles matching their saved search turns a broadcast into the most
useful email on the platform, with no new channel to build.

*Shipped:* saved searches, alerts, and the weekly digest workflow. Note that the
digest skips unverified email addresses — and until 20 August no designer could
verify one, so it had never reached anyone. That is fixed.

### 04 — Applied tracking · SHIPPED

*Shipped:* the Applied tab on the board (self-reported, for roles applied to on an
employer's own site), and later real applications through Kazi.

### 05 — Structured hiring signals · SHIPPED

*Shipped:* skills as structured data matching the designer vocabulary, closing
dates, screening questions, portfolio-required flag.

### 06 — Filtered-vs-cold-start empty states · SHIPPED

Two different failures that were showing one message. *Shipped:* the measured
empty state that counts what each relaxation would recover, and separate copy for
"nothing matches your filters" versus "nothing here yet".

### 07 — Live community teaser · SHIPPED

*Shipped:* the homepage teaser fetches sessions, picks the next scheduled one that
still has seats, and shows its real date, host, length and seats remaining.

Worth keeping the rule it was built with, because it is the honest half: anything
unverifiable leaves the written fallback in place. No upcoming session, a past
date, or a full room all fall back rather than inventing urgency — "a full room is
not an invitation".

*(Recorded here as PARTIAL when this file was first written, from memory rather
than from the code. It was already shipped. Check before you write status down —
that is the same mistake, in miniature, that this file exists because of.)*

---

## Second round — 20 August 2026, application flow

Searched specifically against the internal-applications feature after it was
built. Three findings, all shipped:

- **Wellfound** — [flow](https://mobbin.com/flows/f5960981-c18f-417f-92fd-36568216dd22)
  puts the fit warning *inside* the apply panel, not only on the listing behind
  it: "does not offer visa sponsorship and requires all remote workers to be
  in-country. Your profile indicates you require sponsorship." Plus a softer
  "improve your odds" variant for experience mismatch.
  *Applied:* the apply modal now warns when the role cannot hire from the
  designer's country, using the eligibility verdict the board already computes —
  and still lets them apply.

- **Behance** — [flow](https://mobbin.com/flows/1604217f-3fd0-43e4-a056-289c31f9f7ff)
  renders the profile that will be attached, inside the modal, so you can see what
  the employer sees.
  *Applied:* the apply modal shows the designer's own name, photo and a link to
  preview their profile.

- **Mercor** — [flow](https://mobbin.com/flows/2878ea13-2818-46ae-a120-ed7298b14ab4)
  ends with what happens next and how long it takes, then offers similar
  opportunities.
  *Applied:* the applied state says where the application went, where to see its
  stage, and turns the similar-roles card into "Worth applying to next".

- **Deputy** — [flow](https://mobbin.com/flows/39b243dd-d98c-4a7b-aa0a-53598cee249f)
  is a six-step wizard with a progress sidebar. *Deliberately not applied:* the
  employer already has the designer's profile, so a long form would mostly produce
  a worse version of what is already on that page.

---

## Status, verified 20 August 2026

All seven moves are shipped. Verified against the running site rather than from
memory, after getting move 07's status wrong in the first draft of this file.

Two bugs found while applying the second round, both in the eligibility model the
whole product rests on — and both exactly what move 01 was about, surviving in a
place it had not been checked:

- `openTo()` returned true whenever a badge carried no scope, so an `africa` badge
  answered "open to you" for a designer in the United Kingdom, and a `kenya` badge
  answered "open to you" for Nigeria — which is every employer-posted Kenya role,
  since employers never set a scope. `label()` and `note()` had the same
  conflation and called a Kenya-only role "Open across Africa".
- The job detail page never resolved the viewer's country. The board personalised
  every verdict; the page where someone decides whether to apply was impersonal
  for everyone.

The lesson worth carrying: move 01 was fixed on the board in the first round and
was still wrong on the job page and in the model underneath it. A finding is not
done when the surface it was found on is fixed.

## Caveats that still apply

These were in the original artifact rather than buried, and they have not stopped
being true:

1. **These are patterns from platforms at a scale Path & Pixel has not reached.**
   Contra's earnings strip and Dribbble's seven profile tabs read as confident
   there and would read as empty here. Filter for that every time.

2. **A surface audit tells you what comparable platforms converged on, not what
   your designers want.** Move 01 was the only one worth shipping on the argument
   alone, because it was the promise the site already made.

---

## How to do this again

The Mobbin MCP tools are available in this environment: `search_flows`,
`search_screens`, `search_sections`. They take plain-language descriptions of one
flow or screen at a time, and `platform: "web"` is the right one here.

Two things that made the first round useful and are worth repeating:

- Search one journey per query. Combined queries return mush.
- Check every finding against this codebase before writing it down. Half the
  original findings were "this already exists but is unreachable", which no
  amount of screenshot-reading would have told you.

**And write the results here when you are done.**
