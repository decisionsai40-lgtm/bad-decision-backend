"""
BAD DECISION — Email Senders
=============================
High-level functions that compose + send each transactional email type.
Each function is best-effort: logs on failure but never raises.
"""

from mailer.resend_client import resend
from mailer.templates import (
    render_welcome_email,
    render_payment_receipt_email,
    render_credit_low_email,
    render_subscription_renewed_email,
)


async def send_welcome_email(to_email: str, full_name: str = "") -> bool:
    """Send the welcome email after a new user signs up."""
    subject = "Welcome to Bad Decision — your 50 free credits are ready"
    html = render_welcome_email(full_name=full_name)
    return await resend.send_email(to_email, subject, html)


async def send_payment_receipt_email(
    to_email: str,
    full_name: str,
    credits: int,
    amount_ngn_kobo: int,
    reference: str,
    description: str = "",
) -> bool:
    """Send a payment receipt after a successful Paystack purchase."""
    subject = f"Payment receipt — {credits:,} credits added"
    html = render_payment_receipt_email(
        full_name=full_name,
        credits=credits,
        amount_ngn_kobo=amount_ngn_kobo,
        reference=reference,
        description=description,
    )
    return await resend.send_email(to_email, subject, html)


async def send_credit_low_email(
    to_email: str,
    full_name: str,
    credits_remaining: int,
    tier: str = "free",
) -> bool:
    """Send a warning when user's balance drops to 10 or below."""
    subject = f"You have {credits_remaining} credits left — top up soon"
    html = render_credit_low_email(
        full_name=full_name,
        credits_remaining=credits_remaining,
        tier=tier,
    )
    return await resend.send_email(to_email, subject, html)


async def send_subscription_renewed_email(
    to_email: str,
    full_name: str,
    tier: str,
    credits_granted: int,
    next_billing_date: str,
) -> bool:
    """Send a confirmation when a Paystack subscription renews."""
    subject = f"Your {tier.capitalize()} subscription renewed — {credits_granted:,} credits added"
    html = render_subscription_renewed_email(
        full_name=full_name,
        tier=tier,
        credits_granted=credits_granted,
        next_billing_date=next_billing_date,
    )
    return await resend.send_email(to_email, subject, html)
