"""Regression pass over the behaviour fixed during the August 2026 audit.

Every check here stands for a bug that was live: a verification code with
unlimited guesses, a reset that dropped you at a sign-in form, an approval
nobody was told about, a session selling seats at a meeting that had already
happened, and placeholder content the API happily accepted.

Run it against a local server, never production — it creates and deletes
accounts, listings and sessions:

    KAZI_ADMIN_TOKEN=dev-local-token python3 -u -m uvicorn api.main:app \
        --host 127.0.0.1 --port 8000 --loop asyncio &
    python3 scripts/regression.py

Exits non-zero on the first failing set, so it is usable in CI. It writes to
api/kazi_submissions.db directly for the setup a public API can't reach
(approving an account, moving a session's date into the past), so point it at
a scratch database if the local one has anything worth keeping.
"""
import json, urllib.request, urllib.error, sqlite3, time, sys
B = "http://127.0.0.1:8000"; A = "dev-local-token"
DB = "api/kazi_submissions.db"
passed, failed = [], []

def call(path, data=None, tok=None, method=None):
    req = urllib.request.Request(B + path,
        data=json.dumps(data).encode() if data is not None else None,
        method=method or ("POST" if data is not None else "GET"))
    req.add_header("Content-Type", "application/json")
    if tok: req.add_header("Authorization", "Bearer " + tok)
    try:
        return json.load(urllib.request.urlopen(req, timeout=25))
    except urllib.error.HTTPError as e:
        try: d = json.loads(e.read())
        except Exception: d = {}
        det = d.get("detail")
        return {"HTTP": e.code, "detail": det[0]["msg"] if isinstance(det, list) else det}

def check(name, cond, got=""):
    (passed if cond else failed).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else f"   got: {got}"))

def sql(q, args=()):
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    r = c.execute(q, args).fetchall(); c.commit(); c.close()
    return [dict(x) for x in r]

def exec_(q, args=()):
    c = sqlite3.connect(DB); c.execute(q, args); c.commit(); c.close()

# ---- 1. signup + email verification rate limit -----------------------------
d1 = call("/api/designers/signup", {"email": "r1@reg.test", "password": "password123", "display_name": "Reg One"})
t1 = d1.get("token"); check("signup issues a session", bool(t1), d1)
exec_("UPDATE designers SET status='approved' WHERE email='r1@reg.test'")
for i in range(4):
    call("/api/designers/me/verify-email", {"code": "%06d" % i}, t1)
locked = call("/api/designers/me/verify-email", {"code": "999999"}, t1)
check("verify code locks after 5 wrong", locked.get("HTTP") == 423, locked)
exec_("UPDATE designers SET locked_until='', failed_login_attempts=0 WHERE email='r1@reg.test'")
code = sql("SELECT token FROM designer_email_tokens WHERE purpose='verify' AND used=0 ORDER BY rowid DESC LIMIT 1")[0]["token"]
check("correct code verifies", call("/api/designers/me/verify-email", {"code": code}, t1).get("ok") is True)

# ---- 2. password reset returns a session -----------------------------------
call("/api/designers/forgot-password", {"email": "r1@reg.test"}); time.sleep(1)
rt = sql("SELECT token FROM designer_email_tokens WHERE purpose='reset' AND used=0 ORDER BY rowid DESC LIMIT 1")[0]["token"]
res = call("/api/designers/reset-password", {"token": rt, "new_password": "brandnew12345"})
check("reset hands back a session", bool(res.get("token")), res)
check("reset session works", call("/api/designers/me", tok=res.get("token")).get("display_name") == "Reg One")
check("old session killed by reset", call("/api/designers/me", tok=t1).get("HTTP") == 401)
check("reset token is single use", call("/api/designers/reset-password", {"token": rt, "new_password": "x"*12}).get("HTTP") == 400)
t1 = res["token"]

# ---- 3. admin review speaks ------------------------------------------------
did = sql("SELECT id FROM designers WHERE email='r1@reg.test'")[0]["id"]
exec_("UPDATE designers SET status='pending' WHERE id=%d" % did)
call(f"/api/admin/designers/{did}/approve", {}, A)
n = call("/api/designers/me/notifications", tok=t1).get("notifications", [])
check("approval notifies the designer", any(x["kind"] == "profile_approved" for x in n), [x["kind"] for x in n])
before = len(n)
call(f"/api/admin/designers/{did}/approve", {}, A)
n2 = call("/api/designers/me/notifications", tok=t1).get("notifications", [])
check("re-approving stays silent", len(n2) == before)
rej = call(f"/api/admin/designers/{did}/reject", {"reason": "Add a project first."}, A)
n3 = call("/api/designers/me/notifications", tok=t1).get("notifications", [])
check("rejection carries the reason", any(x["kind"] == "profile_rejected" and "Add a project" in (x["body"] or "") for x in n3))
exec_("UPDATE designers SET status='approved' WHERE id=%d" % did)

# ---- 4. session validation -------------------------------------------------
bad = call("/api/admin/community/sessions", {"title": "X", "session_date": "2026-12-01", "joining_link": "https://meet.google.com/kazi-made-up"}, A)
check("fake Meet code refused", bad.get("HTTP") == 422, bad)
none = call("/api/admin/community/sessions", {"title": "X", "session_date": "2026-12-01", "joining_link": ""}, A)
check("missing joining link refused", none.get("HTTP") == 422, none)
ph = call("/api/admin/community/sessions", {"title": "X", "session_date": "2026-12-01", "joining_link": "https://example.com/m"}, A)
check("placeholder domain refused", ph.get("HTTP") == 422, ph)
good = call("/api/admin/community/sessions", {"title": "Regression Session", "session_date": "2026-12-01", "seats": 3, "joining_link": "https://meet.google.com/abc-defg-hij"}, A)
sid = good.get("session_id"); check("real Meet link accepted", bool(sid), good)
check("an upcoming session books fine", call(f"/api/community/sessions/{sid}/book", {}, t1).get("ok") is True)
exec_("UPDATE community_sessions SET session_date='2020-01-01' WHERE id=%d" % sid)
check("booking a past session refused", call(f"/api/community/sessions/{sid}/book", {}, t1).get("HTTP") == 400)
check("admin can delete a session", call(f"/api/admin/community/sessions/{sid}", None, A, method="DELETE").get("ok") is True)

# ---- 5. topic validation ---------------------------------------------------
check("unknown topic refused", call("/api/community/questions", {"topic": "Nonsense", "title": "T", "body": "B"}, t1).get("HTTP") == 422)
q = call("/api/community/questions", {"topic": "Money", "title": "Reg Q", "body": "B"}, t1)
check("valid topic accepted", bool(q.get("question_id")), q)

# ---- 6. listing url + company website validation ---------------------------
e1 = call("/api/employers/signup", {"email": "re@reg.test", "password": "password123", "full_name": "Reg Emp", "company_name": "Reg Co"})
et = e1.get("token"); check("employer signup works", bool(et), e1)
exec_("UPDATE companies SET status='approved' WHERE name='Reg Co'")
badurl = call("/api/employers/me/listings", {"title": "T", "company": "Reg Co", "location": "Nairobi",
    "url": "https://example.com/apply", "eligibility": "kenya", "discipline": "Product Design",
    "contact_email": "a@b.co", "description": "d"}, et)
check("placeholder apply link refused", badurl.get("HTTP") == 422, badurl)
ok = call("/api/employers/me/listings", {"title": "Reg Role", "company": "Reg Co", "location": "Nairobi",
    "url": "https://a-real-company.co/careers/1", "eligibility": "kenya", "discipline": "Design Engineering",
    "contact_email": "a@b.co", "description": "d", "accepts_applications": True}, et)
subid = ok.get("id"); check("real listing accepted (new discipline)", bool(subid), ok)
check("admin can delete a submission", call(f"/api/admin/submissions/{subid}", None, A, method="DELETE").get("ok") is True)

print()
print(f"  {len(passed)} passed, {len(failed)} failed")
if failed:
    print("  FAILURES:"); [print("   -", f) for f in failed]
sys.exit(1 if failed else 0)
