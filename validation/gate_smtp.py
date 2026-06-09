"""
BAD DECISION AI — Gate 3: SMTP Handshake Check
================================================
Slowest but most important check. Verifies email exists by
connecting to the mail server. Pro tier only.
"""

import smtplib
import dns.resolver
import random
import string
from typing import Tuple


async def check_smtp(email_address: str) -> Tuple[bool, bool]:
    """Verify an email address by connecting to its mail server."""
    if not email_address or email_address == "ABSENT":
        return False, False

    try:
        domain = email_address.split("@")[1]
        mx_records = dns.resolver.resolve(domain, "MX")

        if not mx_records:
            print(f"[GATE3-SMTP] No MX record for {domain}")
            return False, False

        mx_record = str(sorted(mx_records, key=lambda r: r.preference)[0].exchange).rstrip(".")
    except Exception as e:
        print(f"[GATE3-SMTP] MX lookup failed for {domain}: {e}")
        return False, False

    try:
        server = smtplib.SMTP(timeout=10)
        server.connect(mx_record, 25)
        server.ehlo("verify.baddecision.ai")
        server.mail("verify@baddecision.ai")

        code, message = server.rcpt(email_address)
        target_exists = code == 250

        random_user = "".join(random.choices(string.ascii_lowercase, k=15))
        fake_email = f"{random_user}@{domain}"
        code_fake, _ = server.rcpt(fake_email)
        is_catchall = code_fake == 250

        server.quit()

        if is_catchall:
            print(f"[GATE3-SMTP] {email_address} — CATCH-ALL DETECTED")
            return True, True

        if target_exists:
            print(f"[GATE3-SMTP] {email_address} — VERIFIED")
            return True, False

        print(f"[GATE3-SMTP] {email_address} — REJECTED")
        return False, False

    except smtplib.SMTPServerDisconnected:
        return False, False
    except smtplib.SMTPConnectError:
        return False, False
    except Exception as e:
        print(f"[GATE3-SMTP] {email_address} — Error: {e}")
        return True, False  # Don't drop on network errors
