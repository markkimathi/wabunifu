"""
Transactional email via Resend's HTTP API. Used only for designer-account
verification and password-reset links — nothing else in the app sends email
yet. Uses stdlib urllib rather than pulling in a new HTTP-client dependency
for what is, at this point, a single POST call.

Sending failures are logged and swallowed, never raised: a signup or a
password-reset request should still succeed even if the email itself can't
be delivered right now (the designer can always ask an admin, or retry).
"""
from __future__ import annotations
import json
import os
import urllib.request
import urllib.error

# kazi.odana.design is verified as its own domain in Resend (separate DNS
# namespace from the odana.design apex, which stays on Zoho for regular
# mail) — so sending from it never touches Zoho's MX/SPF/DKIM setup.
# Replies still land in a real inbox via reply_to, not this address.
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_ADDRESS = os.environ.get("KAZI_EMAIL_FROM", "Kazi <noreply@kazi.odana.design>")
REPLY_TO_ADDRESS = os.environ.get("KAZI_EMAIL_REPLY_TO", "hello@odana.design")
SITE_URL = os.environ.get("KAZI_SITE_URL", "https://kazi.odana.design")


def send_email(to: str, subject: str, html: str) -> bool:
    if not RESEND_API_KEY:
        print(f"[email] RESEND_API_KEY not set, skipping send to {to}: {subject}")
        return False
    body = json.dumps({
        "from": FROM_ADDRESS, "to": [to], "subject": subject, "html": html,
        "reply_to": REPLY_TO_ADDRESS,
    }).encode()
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
            # Resend's API sits behind Cloudflare, which blocks the default
            # "Python-urllib/x.y" user agent as a bot signature (Cloudflare
            # error 1010) before the request ever reaches Resend itself.
            "User-Agent": "Kazi/1.0 (+https://kazi.odana.design)",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return 200 <= res.status < 300
    except urllib.error.HTTPError as e:
        # Resend's error responses carry a JSON body with the actual reason
        # (e.g. "domain not verified", "invalid api key") — the bare status
        # code alone isn't enough to diagnose a failure.
        detail = e.read().decode(errors="replace")
        print(f"[email] failed to send to {to}: HTTP {e.code} {e.reason} - {detail}")
        return False
    except urllib.error.URLError as e:
        print(f"[email] failed to send to {to}: {e}")
        return False


def send_verification_email(email: str, code: str) -> None:
    send_email(
        email,
        "Your Kazi verification code",
        f"""
        <p>Enter this code to verify your email and finish setting up your Kazi designer profile:</p>
        <p style="font-size:32px;font-weight:700;letter-spacing:.14em;margin:20px 0">{code}</p>
        <p>This code expires in 15 minutes. If you didn't create a Kazi account, you can ignore this email.</p>
        """,
    )


def send_password_reset_email(email: str, token: str) -> None:
    # /signin, and ?token= — not /login?reset=. That link was wrong twice over:
    # /login is a 301 to /signin that dropped the query string, and the auth
    # page reads ?token= rather than ?reset=, so the reset view never opened
    # even when the parameter survived. Designer password reset could not be
    # completed from the email at all.
    link = f"{SITE_URL}/signin?token={token}"
    send_email(
        email,
        "Reset your password for Kazi",
        f"""
        <p>We got a request to reset your Kazi account password.</p>
        <p><a href="{link}">Choose a new password</a></p>
        <p>This link expires in 1 hour. If you didn't request this, you can ignore this email. Your password won't change.</p>
        """,
    )


def send_employer_verification_email(email: str, code: str) -> None:
    send_email(
        email,
        "Your Kazi verification code",
        f"""
        <p>Enter this code to verify your email and finish setting up your company on Kazi:</p>
        <p style="font-size:32px;font-weight:700;letter-spacing:.14em;margin:20px 0">{code}</p>
        <p>This code expires in 15 minutes. If you didn't set up a Kazi hiring account, you can ignore this email.</p>
        """,
    )


def send_employer_password_reset_email(email: str, token: str) -> None:
    # Same clean path the rest of the product uses; the .html form still
    # resolves, but nothing else in the product hands one to a person.
    link = f"{SITE_URL}/signin?role=employer&token={token}"
    send_email(
        email,
        "Reset your password for Kazi",
        f"""
        <p>We got a request to reset the password on your Kazi hiring account.</p>
        <p><a href="{link}">Choose a new password</a></p>
        <p>This link expires in 1 hour. If you didn't request this, you can ignore this email. Your password won't change.</p>
        """,
    )


def send_team_invite_email(email: str, company_name: str, inviter_name: str, token: str) -> None:
    link = f"{SITE_URL}/invite/{token}"
    send_email(
        email,
        f"{inviter_name} invited you to {company_name} on Kazi",
        f"""
        <p>{inviter_name} invited you to post roles for {company_name} on Kazi.</p>
        <p><a href="{link}">See the invite</a></p>
        <p>This link expires in 5 days. If you weren't expecting this, you can ignore this email.</p>
        """,
    )


def send_saved_search_digest(email: str, name: str, search_name: str,
                             jobs: list[dict], country: str) -> bool:
    """New roles matching one saved search. Sent only when there is something to
    report — an email that says "nothing this week" is the fastest way to teach
    someone to ignore the next one."""
    if not jobs:
        return False
    rows = []
    for j in jobs:
        where = j.get("city") or "Remote"
        pay = j.get("pay") or "Not disclosed"
        rows.append(
            f'<tr><td style="padding:14px 0;border-bottom:1px solid #E3E3E6">'
            f'<a href="{SITE_URL}/jobs/{j["id"]}" style="font-size:16px;font-weight:700;color:#0A0A0A;text-decoration:none">'
            f'{j.get("t","")}</a><br>'
            f'<span style="font-size:14px;color:#56565C">{j.get("co","")} &middot; {where} &middot; {pay}</span>'
            f'</td></tr>'
        )
    plural = "role" if len(jobs) == 1 else "roles"
    where_line = (
        f"<p style='font-size:14px;color:#56565C'>Checked against {country}, so everything here is open to you.</p>"
        if country else ""
    )
    return send_email(
        email,
        f"{len(jobs)} new {plural} for “{search_name}”",
        f"""
        <p>Hi {name},</p>
        <p>{len(jobs)} new {plural} came in matching your saved search
           <strong>{search_name}</strong>.</p>
        {where_line}
        <table style="width:100%;border-collapse:collapse;margin:18px 0">{''.join(rows)}</table>
        <p><a href="{SITE_URL}/jobs">See the whole board</a></p>
        <p style="font-size:13px;color:#6E6E75">You're getting this because you turned on alerts for this
           search. Turn them off any time from the bell beside it on the jobs page.</p>
        """,
    )
