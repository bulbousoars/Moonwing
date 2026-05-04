"""Email notification service for Moonwing security events.

Sends non-blocking emails via background threads when SMTP is configured
and the relevant event type is enabled.
"""

from __future__ import annotations

import logging
import smtplib
import threading
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from sqlalchemy.orm import Session

logger = logging.getLogger("moonwing.notifications")

# ── Event type constants ────────────────────────────────────────────
EVENT_RUN_STARTED = "run_started"
EVENT_RUN_COMPLETED = "run_completed"
EVENT_CRITICAL_FINDING = "critical_finding"
EVENT_FINDING_REMEDIATED = "finding_remediated"
EVENT_USER_ADDED = "user_added"
EVENT_USER_DELETED = "user_deleted"
EVENT_USER_PERMISSIONS_CHANGED = "user_permissions_changed"

ALL_EVENT_TYPES = [
    EVENT_RUN_STARTED,
    EVENT_RUN_COMPLETED,
    EVENT_CRITICAL_FINDING,
    EVENT_FINDING_REMEDIATED,
    EVENT_USER_ADDED,
    EVENT_USER_DELETED,
    EVENT_USER_PERMISSIONS_CHANGED,
]

EVENT_LABELS: dict[str, str] = {
    EVENT_RUN_STARTED: "Run Started",
    EVENT_RUN_COMPLETED: "Run Completed",
    EVENT_CRITICAL_FINDING: "Critical Finding Found",
    EVENT_FINDING_REMEDIATED: "Finding Remediated",
    EVENT_USER_ADDED: "User Added",
    EVENT_USER_DELETED: "User Deleted",
    EVENT_USER_PERMISSIONS_CHANGED: "User Permissions Changed",
}


@dataclass(frozen=True)
class SmtpSettings:
    host: str
    port: int
    username: str | None
    password: str | None
    use_tls: bool
    from_address: str
    from_name: str


def _load_smtp_settings(session: Session) -> SmtpSettings | None:
    from moonwing.db.models.smtp_config import SmtpConfig
    from moonwing.services.crypto import CryptoError, decrypt_api_key

    row = session.get(SmtpConfig, 1)
    if not row or not row.enabled:
        return None
    password = None
    if row.encrypted_password:
        try:
            password = decrypt_api_key(row.encrypted_password)
        except CryptoError:
            logger.error("Failed to decrypt SMTP password")
            return None
    return SmtpSettings(
        host=row.host,
        port=row.port,
        username=row.username,
        password=password,
        use_tls=row.use_tls,
        from_address=row.from_address,
        from_name=row.from_name,
    )


def _get_recipients(session: Session, event_type: str) -> list[str]:
    from moonwing.db.models.notification_preference import NotificationPreference

    pref = session.query(NotificationPreference).filter(
        NotificationPreference.event_type == event_type
    ).first()
    if not pref or not pref.enabled:
        return []
    return [e.strip() for e in (pref.recipient_emails or []) if e.strip()]


# ── Email rendering ─────────────────────────────────────────────────

def _email_wrapper(title: str, body_html: str) -> str:
    return (
        '<!DOCTYPE html>'
        '<html><head><meta charset="utf-8"></head>'
        '<body style="margin:0;padding:0;background:#0a0a0f;font-family:Inter,Arial,sans-serif;">'
        '<table width="100%" cellpadding="0" cellspacing="0" style="background:#0a0a0f;padding:32px 0;">'
        '<tr><td align="center">'
        '<table width="560" cellpadding="0" cellspacing="0" style="background:#16161e;border-radius:12px;border:1px solid rgba(255,255,255,0.06);">'
        '<tr><td style="padding:28px 32px 16px;border-bottom:1px solid rgba(255,255,255,0.06);">'
        '  <span style="font-size:18px;font-weight:600;color:#e2e2e8;">Moonwing</span>'
        '  <span style="font-size:12px;color:#6b6b80;margin-left:8px;">Security Platform</span>'
        '</td></tr>'
        '<tr><td style="padding:24px 32px;">'
        f'  <h2 style="margin:0 0 16px;font-size:16px;font-weight:600;color:#e2e2e8;">{title}</h2>'
        f'  {body_html}'
        '</td></tr>'
        '<tr><td style="padding:16px 32px 24px;border-top:1px solid rgba(255,255,255,0.06);">'
        '  <span style="font-size:11px;color:#6b6b80;">This is an automated notification from Moonwing. Do not reply to this email.</span>'
        '</td></tr>'
        '</table>'
        '</td></tr>'
        '</table>'
        '</body></html>'
    )


def _detail_row(label: str, value: str) -> str:
    return (
        f'<tr><td style="padding:4px 0;color:#6b6b80;font-size:13px;width:140px;">{label}</td>'
        f'<td style="padding:4px 0;color:#e2e2e8;font-size:13px;">{value}</td></tr>'
    )


def _render_email(event_type: str, ctx: dict) -> tuple[str, str]:
    """Render subject line and HTML body for the given event type."""

    if event_type == EVENT_RUN_STARTED:
        subject = f"[Moonwing] Scan started \u2014 {ctx.get('job_family', 'unknown')}"
        body = (
            '<table cellpadding="0" cellspacing="0">'
            + _detail_row("Run ID", ctx.get("run_id", "\u2014"))
            + _detail_row("Job Family", ctx.get("job_family", "\u2014"))
            + _detail_row("Provider", ctx.get("provider", "\u2014"))
            + _detail_row("Model", ctx.get("model", "\u2014"))
            + "</table>"
        )
        return subject, _email_wrapper("Scan Started", body)

    if event_type == EVENT_RUN_COMPLETED:
        subject = f"[Moonwing] Scan completed \u2014 {ctx.get('finding_count', 0)} findings"
        body = (
            '<table cellpadding="0" cellspacing="0">'
            + _detail_row("Run ID", ctx.get("run_id", "\u2014"))
            + _detail_row("Job Family", ctx.get("job_family", "\u2014"))
            + _detail_row("Findings", str(ctx.get("finding_count", 0)))
            + "</table>"
        )
        return subject, _email_wrapper("Scan Completed", body)

    if event_type == EVENT_CRITICAL_FINDING:
        sev = ctx.get("severity", "critical").upper()
        subject = f"[Moonwing] CRITICAL finding: {ctx.get('title', 'Unknown')}"
        body = (
            '<div style="background:#3d1216;border:1px solid #7f1d1d;border-radius:8px;padding:12px 16px;margin-bottom:16px;">'
            f'<span style="color:#fca5a5;font-weight:600;font-size:13px;">{sev}</span>'
            f'<span style="color:#e2e2e8;font-size:13px;margin-left:8px;">{ctx.get("title", "\u2014")}</span>'
            '</div>'
            '<table cellpadding="0" cellspacing="0">'
            + _detail_row("Finding ID", ctx.get("finding_id", "\u2014"))
            + _detail_row("Run ID", ctx.get("run_id", "\u2014"))
            + "</table>"
        )
        return subject, _email_wrapper("Critical Finding Detected", body)

    if event_type == EVENT_FINDING_REMEDIATED:
        subject = f"[Moonwing] Finding remediated: {ctx.get('title', 'Unknown')}"
        body = (
            '<div style="background:#052e16;border:1px solid #166534;border-radius:8px;padding:12px 16px;margin-bottom:16px;">'
            '<span style="color:#86efac;font-weight:600;font-size:13px;">REMEDIATED</span>'
            f'<span style="color:#e2e2e8;font-size:13px;margin-left:8px;">{ctx.get("title", "\u2014")}</span>'
            '</div>'
            '<table cellpadding="0" cellspacing="0">'
            + _detail_row("Finding ID", ctx.get("finding_id", "\u2014"))
            + _detail_row("New Status", ctx.get("new_status", "remediated"))
            + _detail_row("Changed By", ctx.get("changed_by", "\u2014"))
            + "</table>"
        )
        return subject, _email_wrapper("Finding Remediated", body)

    if event_type == EVENT_USER_ADDED:
        subject = f"[Moonwing] New user: {ctx.get('display_name', 'Unknown')}"
        body = (
            '<table cellpadding="0" cellspacing="0">'
            + _detail_row("User", ctx.get("display_name", "\u2014"))
            + _detail_row("Email", ctx.get("user_email", "\u2014"))
            + _detail_row("Role", ctx.get("role", "\u2014"))
            + _detail_row("Created By", ctx.get("created_by", "\u2014"))
            + "</table>"
        )
        return subject, _email_wrapper("User Added", body)

    if event_type == EVENT_USER_DELETED:
        subject = f"[Moonwing] User deleted: {ctx.get('display_name', 'Unknown')}"
        body = (
            '<table cellpadding="0" cellspacing="0">'
            + _detail_row("User", ctx.get("display_name", "\u2014"))
            + _detail_row("Email", ctx.get("user_email", "\u2014"))
            + _detail_row("Deleted By", ctx.get("deleted_by", "\u2014"))
            + "</table>"
        )
        return subject, _email_wrapper("User Deleted", body)

    if event_type == EVENT_USER_PERMISSIONS_CHANGED:
        changes = []
        if ctx.get("old_role") and ctx.get("new_role"):
            changes.append(f"Role: {ctx['old_role']} \u2192 {ctx['new_role']}")
        if ctx.get("old_status") and ctx.get("new_status"):
            changes.append(f"Status: {ctx['old_status']} \u2192 {ctx['new_status']}")
        change_text = ", ".join(changes) or "permissions updated"
        subject = f"[Moonwing] Permissions changed: {ctx.get('user_email', 'Unknown')}"
        body = (
            '<table cellpadding="0" cellspacing="0">'
            + _detail_row("User", ctx.get("user_email", "\u2014"))
            + _detail_row("Change", change_text)
            + _detail_row("Changed By", ctx.get("changed_by", "\u2014"))
            + "</table>"
        )
        return subject, _email_wrapper("User Permissions Changed", body)

    subject = f"[Moonwing] {event_type}"
    body = f'<p style="color:#e2e2e8;font-size:13px;">{event_type}</p>'
    return subject, _email_wrapper(event_type, body)


# ── SMTP sending ────────────────────────────────────────────────────

def _send_email_sync(
    settings: SmtpSettings,
    recipients: list[str],
    subject: str,
    html_body: str,
) -> None:
    """Blocking SMTP send. Called from background thread."""
    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = f"{settings.from_name} <{settings.from_address}>"
        msg["To"] = ", ".join(recipients)
        msg["Subject"] = subject
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        if settings.port == 465:
            server = smtplib.SMTP_SSL(settings.host, settings.port, timeout=10)
            server.ehlo()
        elif settings.use_tls:
            server = smtplib.SMTP(settings.host, settings.port, timeout=10)
            server.ehlo()
            server.starttls()
        else:
            server = smtplib.SMTP(settings.host, settings.port, timeout=10)
            server.ehlo()

        if settings.username and settings.password:
            server.login(settings.username, settings.password)

        server.sendmail(settings.from_address, recipients, msg.as_string())
        server.quit()
        logger.info("Notification email sent: %s to %s", subject, recipients)
    except Exception:
        logger.exception("Failed to send notification email: %s", subject)
        raise


def send_test_email(session: Session, recipient: str) -> tuple[bool, str]:
    """Send a test email. Returns (success, message)."""
    settings = _load_smtp_settings(session)
    if not settings:
        return False, "SMTP is not configured or not enabled"
    subject = "[Moonwing] Test notification"
    html = _email_wrapper(
        "Test Email",
        '<p style="color:#e2e2e8;font-size:13px;">This is a test notification from Moonwing. '
        "If you received this, email notifications are working correctly.</p>",
    )
    try:
        _send_email_sync(settings, [recipient], subject, html)
        return True, f"Test email sent to {recipient}"
    except Exception as exc:
        return False, f"Failed to send: {exc}"


# ── Main entry point ────────────────────────────────────────────────

def notify(session: Session, event_type: str, context: dict) -> None:
    """Fire-and-forget notification. Returns immediately.

    Loads SMTP config and preferences from the DB, renders the email,
    and dispatches it in a daemon thread. No-op if SMTP is not
    configured or the event type is disabled.
    """
    try:
        settings = _load_smtp_settings(session)
        if not settings:
            return
        recipients = _get_recipients(session, event_type)
        if not recipients:
            return
        subject, html_body = _render_email(event_type, context)
        thread = threading.Thread(
            target=_send_email_sync,
            args=(settings, recipients, subject, html_body),
            daemon=True,
        )
        thread.start()
    except Exception:
        logger.exception("Error preparing notification for %s", event_type)
