"""
BAD DECISION — Email Templates
===============================
HTML templates for all transactional email types.
All templates use inline CSS (required for email clients).
Brand colors: teal #00a8cc primary, dark teal #003d4d accents.
"""


def _email_shell(content_html: str, greeting: str = "") -> str:
    """Wrapper that gives every email the Bad Decision look."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Bad Decision</title>
</head>
<body style="margin:0;padding:0;background:#f4fafb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#0f1f24;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4fafb;padding:24px 0;">
    <tr>
      <td align="center">
        <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;background:#ffffff;border-radius:16px;overflow:hidden;box-shadow:0 2px 8px rgba(0,61,77,0.06);">

          <!-- Header -->
          <tr>
            <td style="background:#003d4d;padding:24px 32px;text-align:center;">
              <div style="display:inline-block;background:#00a8cc;color:#ffffff;padding:8px 16px;border-radius:10px;font-weight:bold;font-size:18px;letter-spacing:0.5px;">Bad Decision</div>
            </td>
          </tr>

          <!-- Body -->
          <tr>
            <td style="padding:32px;">
              {content_html}
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="padding:24px 32px;background:#f4fafb;border-top:1px solid #e4eef0;text-align:center;">
              <p style="margin:0 0 8px;font-size:13px;color:#5a6a70;">
                Questions? Reply to this email — a real person reads every reply.
              </p>
              <p style="margin:0;font-size:12px;color:#8a9aa0;">
                © 2026 Bad Decision. All rights reserved.<br>
                You are receiving this email because you have an account at baddecision.app.
              </p>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def render_welcome_email(full_name: str = "") -> str:
    """Welcome email sent after signup."""
    greeting = f"Hi {full_name}," if full_name else "Hi there,"
    return _email_shell(f"""
      <h1 style="margin:0 0 16px;font-size:24px;color:#003d4d;">{greeting}</h1>
      <p style="font-size:16px;line-height:1.6;color:#1a2b40;margin:0 0 16px;">
        You are now part of <strong>Bad Decision</strong> — the platform that finds real buyers
        who want what you sell, and tests every email before you pay.
      </p>
      <p style="font-size:16px;line-height:1.6;color:#1a2b40;margin:0 0 16px;">
        <strong>You have 50 free credits to start.</strong> Use them to search for
        local businesses that match your ideal customer. Each lead costs 1 credit.
        Credits renew every 30 days (no accumulation).
      </p>
      <div style="background:#e0f7fa;border-left:4px solid #00a8cc;padding:16px;margin:24px 0;border-radius:0 8px 8px 0;">
        <p style="margin:0;font-size:14px;color:#003d4d;">
          <strong>Quick start:</strong> Open your dashboard, pick a search engine,
          type what you want (e.g., "roofers in Dallas"), and hit search.
        </p>
      </div>
      <div style="text-align:center;margin:32px 0;">
        <a href="https://bad-decision-front-end.vercel.app/dashboard"
           style="background:#00a8cc;color:#ffffff;padding:14px 32px;text-decoration:none;border-radius:10px;font-weight:bold;display:inline-block;font-size:16px;">
          Open Dashboard
        </a>
      </div>
      <p style="font-size:14px;color:#5a6a70;margin:24px 0 0;">
        Happy hunting,<br>The Bad Decision Team
      </p>
    """)


def render_payment_receipt_email(
    full_name: str,
    credits: int,
    amount_ngn_kobo: int,
    reference: str,
    description: str = "",
) -> str:
    """Payment receipt sent after a successful Paystack purchase."""
    greeting = f"Hi {full_name}," if full_name else "Hi there,"
    amount_ngn = amount_ngn_kobo / 100
    amount_formatted = f"₦{amount_ngn:,.0f}"
    return _email_shell(f"""
      <h1 style="margin:0 0 16px;font-size:24px;color:#003d4d;">{greeting}</h1>
      <p style="font-size:16px;line-height:1.6;color:#1a2b40;margin:0 0 16px;">
        Your payment was successful. Your credits have been added to your account.
      </p>

      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:24px 0;border-collapse:collapse;">
        <tr>
          <td style="padding:12px 16px;background:#f4fafb;border-bottom:1px solid #e4eef0;font-size:14px;color:#5a6a70;">Description</td>
          <td style="padding:12px 16px;background:#f4fafb;border-bottom:1px solid #e4eef0;font-size:14px;color:#0f1f24;font-weight:600;text-align:right;">
            {description or f"{credits} credit top-up"}
          </td>
        </tr>
        <tr>
          <td style="padding:12px 16px;border-bottom:1px solid #e4eef0;font-size:14px;color:#5a6a70;">Credits added</td>
          <td style="padding:12px 16px;border-bottom:1px solid #e4eef0;font-size:14px;color:#0f1f24;font-weight:600;text-align:right;">
            {credits.toLocaleString() if hasattr(credits, 'toLocaleString') else f"{credits:,}"} credits
          </td>
        </tr>
        <tr>
          <td style="padding:12px 16px;border-bottom:1px solid #e4eef0;font-size:14px;color:#5a6a70;">Amount paid</td>
          <td style="padding:12px 16px;border-bottom:1px solid #e4eef0;font-size:14px;color:#0f1f24;font-weight:600;text-align:right;">
            {amount_formatted}
          </td>
        </tr>
        <tr>
          <td style="padding:12px 16px;border-bottom:1px solid #e4eef0;font-size:14px;color:#5a6a70;">Reference</td>
          <td style="padding:12px 16px;border-bottom:1px solid #e4eef0;font-size:13px;color:#5a6a70;text-align:right;font-family:monospace;">
            {reference}
          </td>
        </tr>
        <tr>
          <td style="padding:12px 16px;font-size:14px;color:#5a6a70;">Credit expiry</td>
          <td style="padding:12px 16px;font-size:14px;color:#0f1f24;font-weight:600;text-align:right;">
            60 days from today
          </td>
        </tr>
      </table>

      <div style="background:#fef9e7;border-left:4px solid #d4a017;padding:16px;margin:24px 0;border-radius:0 8px 8px 0;">
        <p style="margin:0;font-size:14px;color:#5a4a10;">
          <strong>Note:</strong> Paid credits expire 60 days from purchase. Free credits
          (50/month) renew every 30 days separately. Use them before they expire.
        </p>
      </div>

      <div style="text-align:center;margin:32px 0;">
        <a href="https://bad-decision-front-end.vercel.app/dashboard"
           style="background:#00a8cc;color:#ffffff;padding:14px 32px;text-decoration:none;border-radius:10px;font-weight:bold;display:inline-block;font-size:16px;">
          View My Credits
        </a>
      </div>
      <p style="font-size:14px;color:#5a6a70;margin:24px 0 0;">
        Thanks for your purchase,<br>The Bad Decision Team
      </p>
    """)


def render_credit_low_email(full_name: str, credits_remaining: int, tier: str = "free") -> str:
    """Warning email when user's balance drops to 10 or below."""
    greeting = f"Hi {full_name}," if full_name else "Hi there,"
    if tier == "free":
        upgrade_cta = "Upgrade to a paid plan for more credits, more engines, and higher daily limits."
    else:
        upgrade_cta = "Top up your credits to keep finding leads without interruption."

    return _email_shell(f"""
      <h1 style="margin:0 0 16px;font-size:24px;color:#003d4d;">{greeting}</h1>
      <p style="font-size:16px;line-height:1.6;color:#1a2b40;margin:0 0 16px;">
        You have <strong>{credits_remaining} credits remaining</strong> on your Bad Decision account.
        Once you run out, you won't be able to run new searches until your credits renew
        or you purchase more.
      </p>
      <div style="background:#fef9e7;border-left:4px solid #d4a017;padding:16px;margin:24px 0;border-radius:0 8px 8px 0;">
        <p style="margin:0;font-size:14px;color:#5a4a10;">
          <strong>Don't run out mid-search.</strong> {upgrade_cta}
        </p>
      </div>
      <div style="text-align:center;margin:32px 0;">
        <a href="https://bad-decision-front-end.vercel.app/pricing"
           style="background:#00a8cc;color:#ffffff;padding:14px 32px;text-decoration:none;border-radius:10px;font-weight:bold;display:inline-block;font-size:16px;">
          Get More Credits
        </a>
      </div>
      <p style="font-size:14px;color:#5a6a70;margin:24px 0 0;">
        The Bad Decision Team
      </p>
    """)


def render_subscription_renewed_email(
    full_name: str,
    tier: str,
    credits_granted: int,
    next_billing_date: str,
) -> str:
    """Email sent when a Paystack subscription renews successfully."""
    greeting = f"Hi {full_name}," if full_name else "Hi there,"
    tier_name = tier.capitalize()
    return _email_shell(f"""
      <h1 style="margin:0 0 16px;font-size:24px;color:#003d4d;">{greeting}</h1>
      <p style="font-size:16px;line-height:1.6;color:#1a2b40;margin:0 0 16px;">
        Your <strong>{tier_name}</strong> subscription has renewed successfully.
        Your monthly credits have been added to your account.
      </p>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:24px 0;border-collapse:collapse;">
        <tr>
          <td style="padding:12px 16px;background:#f4fafb;border-bottom:1px solid #e4eef0;font-size:14px;color:#5a6a70;">Plan</td>
          <td style="padding:12px 16px;background:#f4fafb;border-bottom:1px solid #e4eef0;font-size:14px;color:#0f1f24;font-weight:600;text-align:right;">
            {tier_name}
          </td>
        </tr>
        <tr>
          <td style="padding:12px 16px;border-bottom:1px solid #e4eef0;font-size:14px;color:#5a6a70;">Credits added</td>
          <td style="padding:12px 16px;border-bottom:1px solid #e4eef0;font-size:14px;color:#0f1f24;font-weight:600;text-align:right;">
            {credits_granted:,} credits
          </td>
        </tr>
        <tr>
          <td style="padding:12px 16px;font-size:14px;color:#5a6a70;">Next billing date</td>
          <td style="padding:12px 16px;font-size:14px;color:#0f1f24;font-weight:600;text-align:right;">
            {next_billing_date}
          </td>
        </tr>
      </table>
      <div style="text-align:center;margin:32px 0;">
        <a href="https://bad-decision-front-end.vercel.app/dashboard"
           style="background:#00a8cc;color:#ffffff;padding:14px 32px;text-decoration:none;border-radius:10px;font-weight:bold;display:inline-block;font-size:16px;">
          Open Dashboard
        </a>
      </div>
      <p style="font-size:14px;color:#5a6a70;margin:24px 0 0;">
        Thanks for being a subscriber,<br>The Bad Decision Team
      </p>
    """)
