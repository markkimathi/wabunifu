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
    link = f"{SITE_URL}/account?reset={token}"
    send_email(
        email,
        "Reset your password for Kazi",
        f"""
        <p>We got a request to reset your Kazi account password.</p>
        <p><a href="{link}">Choose a new password</a></p>
        <p>This link expires in 1 hour. If you didn't request this, you can ignore this email. Your password won't change.</p>
        """,
    )
