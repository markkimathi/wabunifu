"""
Offline sample payloads in the neutral 'common' shape, so `run.py --sample`
produces a real jobs.json without hitting the network. Mirrors what
greenhouse.to_common / lever.to_common return. Includes deliberate edge cases:
non-design roles (to prove the classifier rejects them), a region-locked remote
role (-> 'check'), and a duplicate (-> deduped).
"""

SAMPLE_COMMON = [
    {"title": "Senior Product Designer", "company": "Flutterwave", "department": "Design",
     "location": "Lagos, Nigeria", "body": "Hybrid. Join our design team.",
     "url": "https://flutterwave.com/careers/senior-product-designer", "source": "Greenhouse",
     "updated_at": "2026-07-27"},

    {"title": "Product Designer", "company": "M-KOPA", "department": "Product",
     "location": "Nairobi, Kenya", "body": "Hybrid role based in Nairobi.",
     "url": "https://m-kopa.com/careers/product-designer", "source": "Greenhouse",
     "updated_at": "2026-07-26"},

    {"title": "UX Designer", "company": "Andela", "department": "Design",
     "location": "Remote", "body": "Fully remote, open to candidates across Africa. $45k-$70k",
     "url": "https://andela.com/careers/ux-designer", "source": "Lever", "updated_at": "2026-07-27"},

    {"title": "Staff Product Designer", "company": "GitLab", "department": "Design",
     "location": "Remote", "body": "Remote, worldwide. Compensation $120,000-$160,000 USD.",
     "url": "https://about.gitlab.com/jobs/staff-product-designer", "source": "Greenhouse",
     "updated_at": "2026-07-25"},

    {"title": "Product Designer", "company": "Zapier", "department": "Design",
     "location": "Remote", "body": "Remote - Americas only. Must reside in the US or Canada. $95k-$130k",
     "url": "https://zapier.com/jobs/product-designer", "source": "Greenhouse", "updated_at": "2026-07-26"},

    {"title": "Brand & Visual Designer", "company": "Chipper Cash", "department": "Brand",
     "location": "Remote", "body": "Remote across Africa. Own our visual identity.",
     "url": "https://chippercash.com/careers/brand-designer", "source": "Lever", "updated_at": "2026-07-24"},

    {"title": "Motion Designer", "company": "Yoco", "department": "Creative",
     "location": "Cape Town, South Africa", "body": "Hybrid, Cape Town.",
     "url": "https://yoco.com/careers/motion-designer", "source": "Greenhouse", "updated_at": "2026-07-22"},

    {"title": "Design Systems Engineer", "company": "Moniepoint", "department": "Design",
     "location": "Remote", "body": "Remote, EMEA. Build and own our design system.",
     "url": "https://moniepoint.com/careers/design-systems", "source": "Lever", "updated_at": "2026-07-24"},

    {"title": "UX Researcher", "company": "Sun King", "department": "Product",
     "location": "Nairobi, Kenya", "body": "Hybrid. Run user research across markets.",
     "url": "https://sunking.com/careers/ux-researcher", "source": "Greenhouse", "updated_at": "2026-07-21"},

    # --- edge cases the pipeline must handle ---
    # duplicate of the Andela role via a different source -> should dedupe away
    {"title": "UX Designer (Remote)", "company": "Andela", "department": "Design",
     "location": "Remote - Africa", "body": "Remote across Africa.",
     "url": "https://boards.greenhouse.io/andela/ux-designer", "source": "Greenhouse",
     "updated_at": "2026-07-27"},

    # NOT a design role -> classifier must reject
    {"title": "Design Engineer (Frontend)", "company": "GitLab", "department": "Engineering",
     "location": "Remote", "body": "Build UI in React. Strong TypeScript.",
     "url": "https://about.gitlab.com/jobs/design-engineer", "source": "Greenhouse", "updated_at": "2026-07-20"},

    # NOT a design role -> reject
    {"title": "Sales Development Representative", "company": "Flutterwave", "department": "Sales",
     "location": "Lagos, Nigeria", "body": "Hit quota.",
     "url": "https://flutterwave.com/careers/sdr", "source": "Greenhouse", "updated_at": "2026-07-20"},
]
