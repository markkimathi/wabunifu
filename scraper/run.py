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

from models import Job
from pipeline.classify import classify_discipline, classify_level
from pipeline.eligibility import infer_eligibility
from pipeline.normalize import normalize_work_type, clean_location, guess_country, extract_salary
from pipeline.dedupe import dedupe

OUT = Path(__file__).parent.parent / "web" / "jobs.json"


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
    eligibility = infer_eligibility(location, work_type, region_text=body)
    salary = extract_salary(body, common.get("salary", ""))

    posted = common.get("updated_at") or ""
    if not posted and common.get("created_ms"):
        posted = datetime.fromtimestamp(common["created_ms"] / 1000).date().isoformat()

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
        salary=salary,
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
    for c in COMPANIES:
        mod = FETCHERS.get(c["ats"])
        if mod is None:
            print(f"  ! no fetcher for ats={c['ats']} ({c['name']}), skipping", file=sys.stderr)
            continue
        try:
            raw = mod.fetch(c["token"])
            out += [mod.to_common(r, c["name"], c["token"]) for r in raw]
            print(f"  ok  {c['name']}: {len(raw)} listings")
        except Exception as e:
            print(f"  !! {c['name']} failed: {e}", file=sys.stderr)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="store_true", help="use offline fixtures instead of live fetch")
    args = ap.parse_args()

    print("Kazi pipeline:", "SAMPLE mode" if args.sample else "LIVE mode")
    common = gather_sample() if args.sample else gather_live()
    print(f"fetched {len(common)} raw listings")

    jobs = [j for j in (build_job(c) for c in common) if j]
    print(f"{len(jobs)} design roles after classification")

    jobs = dedupe(jobs)
    print(f"{len(jobs)} after dedupe")

    jobs.sort(key=lambda j: j.days_ago())  # newest first

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "count": len(jobs),
        "jobs": [j.to_web() for j in jobs],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"wrote {OUT} ({len(jobs)} jobs)")

    # quick eligibility breakdown: useful sanity check
    from collections import Counter
    breakdown = Counter(j.eligibility for j in jobs)
    print("eligibility:", dict(breakdown))


if __name__ == "__main__":
    main()
