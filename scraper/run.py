#!/usr/bin/env python3
"""
Kazi pipeline entrypoint.

  python run.py --sample     # run on offline fixtures (no network) -> web/jobs.json
  python run.py              # run live against companies.py  (needs network)

Flow:  fetch -> filter to design roles -> tag discipline/level/eligibility
       -> normalize -> dedupe -> sort -> write web/jobs.json
"""
from __future__ import annotations
import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from models import Job, MAX_AGE_DAYS
from pipeline.classify import classify_discipline, classify_level
from pipeline.eligibility import infer_eligibility_detail
from pipeline.employment import infer_employment_type, employment_was_stated
from pipeline.normalize import normalize_work_type, clean_location, guess_country, extract_salary
from pipeline.dedupe import dedupe
from desc_format import format_description

OUT = Path(__file__).parent.parent / "web" / "jobs.json"

# Guard rails for a run that half-worked. Both are deliberately loose: they are
# meant to catch an ATS changing its API under us, not an ordinary quiet week.
MAX_BROKEN_SHARE = 0.25          # more than a quarter of sources failing is systemic
MAX_DROP_RATIO = 0.5             # publishing fewer than half the previous roles
MIN_BOARD_FOR_DROP_CHECK = 20    # ...but only once the board is big enough to judge


def build_job(common: dict) -> Job | None:
    """common dict (from a source's to_common) -> a classified Job, or None if not design."""
    title = common.get("title", "")
    dept = common.get("department", "")

    discipline = classify_discipline(title, dept)
    if discipline is None:
        return None  # not a design role we list

    location = clean_location(common.get("location", ""))
    body = common.get("body", "")
    work_type = normalize_work_type(location, remote_flag=common.get("remote_flag"), body=body)
    eligibility, eligibility_scope = infer_eligibility_detail(location, work_type, region_text=body)
    ats_etype = common.get("employment_type", "") or common.get("commitment", "")
    employment_type = infer_employment_type(title, body, ats_etype)
    employment_stated = employment_was_stated(title, body, ats_etype)
    if work_type != "Remote" and eligibility not in ("kenya", "africa"):
        return None  # hybrid/on-site only allowed when the role itself is based in Africa
    salary = extract_salary(body, common.get("salary", ""))

    posted = common.get("updated_at") or ""
    if not posted and common.get("created_ms"):
        posted = datetime.fromtimestamp(common["created_ms"] / 1000).date().isoformat()

    body_html = common.get("body_html", "")
    desc_html, desc_text = format_description(body_html or body, is_html=bool(body_html))

    return Job(
        title=title.strip(),
        company=common.get("company", "").strip(),
        url=common.get("url", ""),
        source=common.get("source", ""),
        location=location,
        country=guess_country(location),
        work_type=work_type,
        discipline=discipline,
        level=classify_level(title),
        eligibility=eligibility,
        eligibility_scope=eligibility_scope,
        employment_type=employment_type,
        employment_stated=employment_stated,
        salary=salary,
        desc=desc_html or None,
        desc_text=desc_text or None,
        posted_at=posted or date.today().isoformat(),
    )


def gather_sample() -> list[dict]:
    from sources.fixtures import SAMPLE_COMMON
    return list(SAMPLE_COMMON)


def gather_live() -> list[dict]:
    from sources import greenhouse, lever, ashby, breezy, hibob, pinpoint, chippercash
    from sources.companies import COMPANIES

    # ats value -> module. Adding a new ATS is just: write sources/<x>.py with
    # fetch(token) and to_common(raw, company, token), then add one line here.
    FETCHERS = {
        "greenhouse": greenhouse,
        "lever": lever,
        "ashby": ashby,
        "breezy": breezy,
        "hibob": hibob,
        "pinpoint": pinpoint,
        "chippercash": chippercash,
    }

    out: list[dict] = []
    broken: list[str] = []
    for c in COMPANIES:
        mod = FETCHERS.get(c["ats"])
        if mod is None:
            print(f"  ! no fetcher for ats={c['ats']} ({c['name']}), skipping", file=sys.stderr)
            broken.append(c["name"])
            continue
        try:
            raw = mod.fetch(c["token"])
            out += [mod.to_common(r, c["name"], c["token"]) for r in raw]
            print(f"  ok  {c['name']}: {len(raw)} listings")
        except Exception as e:
            print(f"  !! {c['name']} failed: {e}", file=sys.stderr)
            broken.append(c["name"])
    return out, broken, len(COMPANIES)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="store_true", help="use offline fixtures instead of live fetch")
    ap.add_argument("--force", action="store_true",
                    help="publish even if many sources failed or the board collapsed")
    args = ap.parse_args()

    print("Kazi pipeline:", "SAMPLE mode" if args.sample else "LIVE mode")
    if args.sample:
        common, broken, total = gather_sample(), [], 0
    else:
        common, broken, total = gather_live()
    print(f"fetched {len(common)} raw listings")

    # Every fetch error is caught so one dead host cannot take the run down
    # with it. That is right, and it also meant a run where most sources
    # failed finished green, wrote a much smaller jobs.json, and deployed it —
    # the board would quietly shrink with nothing to show it had.
    if broken and total:
        share = len(broken) / total
        print(f"{len(broken)}/{total} sources failed: {', '.join(broken[:8])}"
              + (" ..." if len(broken) > 8 else ""), file=sys.stderr)
        if share > MAX_BROKEN_SHARE and not args.force:
            sys.exit(f"refusing to publish: {share:.0%} of sources failed "
                     f"(limit {MAX_BROKEN_SHARE:.0%}). Re-run with --force to publish anyway.")

    jobs = [j for j in (build_job(c) for c in common) if j]
    print(f"{len(jobs)} design roles after classification")

    jobs = dedupe(jobs)
    print(f"{len(jobs)} after dedupe")

    jobs = [j for j in jobs if j.days_ago() <= MAX_AGE_DAYS]
    print(f"{len(jobs)} after dropping listings older than {MAX_AGE_DAYS} days")

    jobs.sort(key=lambda j: j.days_ago())  # newest first

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "count": len(jobs),
        "jobs": [j.to_web() for j in jobs],
    }
    previous = 0
    try:
        previous = int(json.loads(OUT.read_text()).get("count") or 0)
    except Exception:
        previous = 0
    # A board that halves overnight is a broken fetch, not churn. Roles do come
    # and go, so this only bites when the previous board was substantial enough
    # for the drop to mean something. Never in --sample: a dozen fixtures are
    # supposed to be smaller than the live board, and local testing should not
    # need a flag to write its own output.
    if (not args.sample and previous >= MIN_BOARD_FOR_DROP_CHECK
            and len(jobs) < previous * MAX_DROP_RATIO and not args.force):
        sys.exit(f"refusing to publish: {len(jobs)} roles, down from {previous}. "
                 f"That is more than a {(1 - MAX_DROP_RATIO):.0%} drop, which is usually a "
                 f"broken source rather than a quiet week. Re-run with --force to publish anyway.")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"wrote {OUT} ({len(jobs)} jobs, previously {previous})")

    # quick eligibility breakdown: useful sanity check
    from collections import Counter
    breakdown = Counter(j.eligibility for j in jobs)
    print("eligibility:", dict(breakdown))


if __name__ == "__main__":
    main()
