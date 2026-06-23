"""
BAD DECISION — Resend API Client
=================================
Singleton client for sending transactional emails via Resend.
Only initializes if RESEND_API_KEY is set — silent no-op otherwise
(so dev environments without a key don't crash).

Usage:
    from mailer.resend_client import resend
    await resend.send_email(
        to_email="user@example.com",
        subject="Welcome to Bad Decision",
        html_body="<h1>Hi there!</h1>",
    )
"""

import httpx
from config import RESEND_API_KEY, RESEND_FROM_EMAIL, RESEND_API_BASE


class ResendClient:
    """Singleton Resend client. No-op if RESEND_API_KEY is missing."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._enabled = bool(RESEND_API_KEY)
            cls._instance._from_email = RESEND_FROM_EMAIL
        return cls._instance

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def send_email(
        self,
        to_email: str,
        subject: str,
        html_body: str,
        reply_to: str = None,
    ) -> bool:
        """
        Send a transactional email via Resend.

        Args:
            to_email: Recipient email address
            subject: Email subject line
            html_body: HTML content of the email
            reply_to: Optional reply-to address

        Returns:
            True on success, False on failure (or if disabled).
            Failures are logged but never raise — email is best-effort.
        """
        if not self._enabled:
            print(f"[RESEND] Disabled — would have sent '{subject}' to {to_email}")
            return False

        if not to_email or "@" not in to_email:
            print(f"[RESEND] Invalid recipient: {to_email}")
            return False

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    RESEND_API_BASE,
                    headers={
                        "Authorization": f"Bearer {RESEND_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "from": self._from_email,
                        "to": [to_email],
                        "subject": subject,
                        "html": html_body,
                        **({"reply_to": reply_to} if reply_to else {}),
                    },
                )

                if response.status_code in (200, 201):
                    print(f"[RESEND] Sent '{subject}' to {to_email}")
                    return True
                else:
                    print(f"[RESEND] Error {response.status_code}: {response.text[:300]}")
                    return False

        except httpx.TimeoutException:
            print(f"[RESEND] Timeout sending to {to_email}")
            return False
        except Exception as e:
            print(f"[RESEND] Exception sending to {to_email}: {e}")
            return False


# Singleton accessor
resend = ResendClient()
