"""
BAD DECISION — Gate 2: SMTP Mailbox Verification
=================================================
This is the MEDIUM-speed check. We connect to the email server and
ask: "Does this mailbox actually exist?"

How it works:
  1. Find the mail server (MX record) for the email's domain.
  2. Connect to it on port 25.
  3. Say hello (EHLO).
  4. Say who we're sending from (MAIL FROM).
  5. Ask if the recipient exists (RCPT TO).
  6. If the server says "OK" → the email exists.
  7. If the server says "No such user" → the email doesn't exist.

Catch-All Trap: Some servers say "yes" to EVERY address (even fake ones).
We detect this by testing a random fake address. If the server accepts
the fake one too, we flag it as is_catchall = TRUE.

Speed: Slow (2-10 seconds per email)
Who gets it: Starter, Growth, Pro (NOT Free tier)
"""

import smtplib
import dns.resolver
import random
import string
from typing import Tuple

from config import SMTP_TIMEOUT


async def check_smtp(email_address: str) -> Tuple[bool, bool]:
    """
    Verify an email address by connecting to its mail server.

    Args:
        email_address: The email to verify (e.g., "john@abcroofing.com")

    Returns:
        (is_valid, is_catchall)
        - is_valid: True = email likely exists, False = email doesn't exist
        - is_catchall: True = server accepts everything (unreliable)
    """
    if not email_address or email_address == "ABSENT":
        return False, False

    if "@" not in email_address:
        return False, False

    try:
        domain = email_address.split("@")[1]
        mx_records = dns.resolver.resolve(domain, "MX")

        if not mx_records:
            print(f"[GATE2-SMTP] No MX record for {domain}")
            return False, False

        mx_record = str(sorted(mx_records, key=lambda r: r.preference)[0].exchange).rstrip(".")
        print(f"[GATE2-SMTP] Mail server for {domain}: {mx_record}")

    except Exception as e:
        print(f"[GATE2-SMTP] MX lookup failed for {domain}: {e}")
        return False, False

    try:
        # Use the SMTP_TIMEOUT from config (default 5s — was hardcoded 10s).
        # Render blocks outbound port 25 so most connects fail fast anyway;
        # don't waste 10s per lead on hosts that silently drop the SYN.
        server = smtplib.SMTP(timeout=SMTP_TIMEOUT)
        server.connect(mx_record, 25)
        server.ehlo("verify.baddecision.app")
        server.mail("verify@baddecision.app")

        # Check if the target email exists
        code, message = server.rcpt(email_address)
        target_exists = code == 250

        # Catch-All Trap: test a random fake address
        random_user = "".join(random.choices(string.ascii_lowercase, k=15))
        fake_email = f"{random_user}@{domain}"
        code_fake, _ = server.rcpt(fake_email)
        is_catchall = code_fake == 250

        server.quit()

        if is_catchall:
            print(f"[GATE2-SMTP] {email_address} — CATCH-ALL DETECTED")
            return True, True

        if target_exists:
            print(f"[GATE2-SMTP] {email_address} — VERIFIED")
            return True, False

        print(f"[GATE2-SMTP] {email_address} — REJECTED (mailbox does not exist)")
        return False, False

    except smtplib.SMTPServerDisconnected:
        print(f"[GATE2-SMTP] {email_address} — Server disconnected (lenient accept)")
        return True, False

    except smtplib.SMTPConnectError:
        print(f"[GATE2-SMTP] {email_address} — Connection failed (lenient accept)")
        return True, False

    except (TimeoutError, OSError) as e:
        # Network unreachable, connection refused, timeout — Render blocks port 25.
        # Don't drop leads because we can't reach the SMTP server.
        # The MX record exists (we already verified that), so the domain CAN receive email.
        print(f"[GATE2-SMTP] {email_address} — Network error ({e}), lenient accept")
        return True, False

    except Exception as e:
        err_str = str(e).lower()
        if 'network is unreachable' in err_str or 'connection refused' in err_str or 'timed out' in err_str:
            print(f"[GATE2-SMTP] {email_address} — Network error (lenient accept): {e}")
            return True, False
        print(f"[GATE2-SMTP] {email_address} — Error: {e} (lenient accept)")
        return True, False
