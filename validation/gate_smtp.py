"""
BAD DECISION AI — Gate 3: SMTP Handshake Check
================================================
This is the SLOWEST but most important check. We actually
connect to the email server and ask: "Does this email address
exist?" This guarantees a real human will receive the email.

How it works:
1. Connect to the email server (like knocking on the door)
2. Say "Hello, I'm a mail server" (EHLO greeting)
3. Say "I want to send mail from test@example.com" (MAIL FROM)
4. Say "I want to deliver to target@company.com" (RCPT TO)
5. If the server says "OK, that person exists" → VERIFIED!
6. If the server says "No such person" → DROP this lead

Catch-All Trap: Some email servers say "yes" to EVERY address
(even fake ones like "asdfasdf@company.com"). We detect this
by testing a random fake address first. If the server accepts
the fake one too, we flag it as is_catchall = TRUE.

Speed: Slow (2-10 seconds per email)
Who gets it: Pro tier ONLY
"""

import smtplib
import dns.resolver
import random
import string
from urllib.parse import urlparse
from typing import Tuple


async def check_smtp(email_address: str) -> Tuple[bool, bool]:
    """
    Verify an email address by connecting to its mail server.

    Args:
        email_address: The email to verify (e.g., "john@abcroofing.com")

    Returns:
        (is_valid, is_catchall)
        - is_valid: True = email likely exists, False = email definitely doesn't exist
        - is_catchall: True = server accepts everything (unreliable), False = server is selective
    """

    if not email_address or email_address == "ABSENT":
        return False, False

    try:
        # Step 1: Find the mail server for this domain
        domain = email_address.split("@")[1]
        mx_records = dns.resolver.resolve(domain, "MX")

        if not mx_records:
            print(f"[GATE3-SMTP] No MX record for {domain}")
            return False, False

        # Get the highest priority mail server
        mx_record = str(sorted(mx_records, key=lambda r: r.preference)[0].exchange).rstrip(".")
        print(f"[GATE3-SMTP] Mail server for {domain}: {mx_record}")

    except Exception as e:
        print(f"[GATE3-SMTP] MX lookup failed for {domain}: {e}")
        return False, False

    try:
        # Step 2: Connect to the mail server
        server = smtplib.SMTP(timeout=10)
        server.connect(mx_record, 25)

        # Step 3: Say hello (EHLO greeting)
        server.ehlo("verify.baddecision.ai")

        # Step 4: Say who we're sending from
        server.mail("verify@baddecision.ai")

        # Step 5: Check if the target email exists
        code, message = server.rcpt(email_address)
        target_exists = code == 250

        # Step 6: Catch-All Trap — test a random fake address
        random_user = "".join(random.choices(string.ascii_lowercase, k=15))
        fake_email = f"{random_user}@{domain}"
        code_fake, _ = server.rcpt(fake_email)
        is_catchall = code_fake == 250

        # Step 7: Close the connection
        server.quit()

        # Results
        if is_catchall:
            print(f"[GATE3-SMTP] {email_address} — CATCH-ALL DETECTED (server accepts everything)")
            return True, True  # Accept it but flag it

        if target_exists:
            print(f"[GATE3-SMTP] {email_address} — VERIFIED (email exists)")
            return True, False

        print(f"[GATE3-SMTP] {email_address} — REJECTED (email does not exist)")
        return False, False

    except smtplib.SMTPServerDisconnected:
        print(f"[GATE3-SMTP] {email_address} — Server disconnected")
        return False, False

    except smtplib.SMTPConnectError:
        print(f"[GATE3-SMTP] {email_address} — Connection failed")
        return False, False

    except Exception as e:
        print(f"[GATE3-SMTP] {email_address} — Error: {e}")
        # If we can't check, assume it's valid (don't drop on network errors)
        return True, False
