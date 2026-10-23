"""
Email notifications for ticket updates.

Email is optional. When MAIL_SERVER is not configured every call here is a
harmless no-op, so the app keeps working in development and in tests.
A failure to send an email must never break the request that triggered it,
so all errors are logged and swallowed.
"""
import logging
import smtplib
import threading
from email.message import EmailMessage

logger = logging.getLogger(__name__)


def mail_is_configured(app):
    return bool(app.config.get("MAIL_SERVER"))


def _deliver(config, message):
    """Open an SMTP connection and send one prepared message."""
    server = config["MAIL_SERVER"]
    port = config["MAIL_PORT"]
    timeout = config["MAIL_TIMEOUT"]

    if config["MAIL_USE_SSL"]:
        smtp = smtplib.SMTP_SSL(server, port, timeout=timeout)
    else:
        smtp = smtplib.SMTP(server, port, timeout=timeout)

    try:
        if config["MAIL_USE_TLS"] and not config["MAIL_USE_SSL"]:
            smtp.starttls()

        if config.get("MAIL_USERNAME"):
            smtp.login(config["MAIL_USERNAME"], config["MAIL_PASSWORD"] or "")

        smtp.send_message(message)
    finally:
        try:
            smtp.quit()
        except Exception:  # pragma: no cover - best effort close
            pass


def _safe_deliver(config, message):
    try:
        _deliver(config, message)
    except Exception:
        logger.exception("Could not send notification email")


def send_email(app, to_address, subject, body):
    """Send a plain text email. Returns True if it was handed off."""
    if not to_address or not mail_is_configured(app):
        return False

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = app.config["MAIL_DEFAULT_SENDER"]
    message["To"] = to_address
    message.set_content(body)

    # Copy the settings so the worker thread does not need an app context.
    config = {
        key: app.config.get(key)
        for key in (
            "MAIL_SERVER",
            "MAIL_PORT",
            "MAIL_USE_TLS",
            "MAIL_USE_SSL",
            "MAIL_USERNAME",
            "MAIL_PASSWORD",
            "MAIL_TIMEOUT",
        )
    }

    if app.config.get("MAIL_ASYNC", True) and not app.config.get("TESTING"):
        threading.Thread(
            target=_safe_deliver,
            args=(config, message),
            daemon=True,
        ).start()
    else:
        _safe_deliver(config, message)

    return True


def _ticket_url(ticket_id):
    # Imported lazily so this module has no hard dependency on a request.
    from flask import url_for

    try:
        return url_for("ticket_detail", ticket_id=ticket_id, _external=True)
    except RuntimeError:
        return ""


def _notify(app, recipient, actor, subject, intro, ticket, extra=None):
    """Email `recipient` about `ticket`, unless they caused the change."""
    if recipient is None or not getattr(recipient, "email", None):
        return False

    if actor is not None and recipient.id == actor.id:
        return False

    lines = [
        f"Hi {recipient.name},",
        "",
        intro,
        "",
        f"Ticket #{ticket.id}: {ticket.title}",
        f"Status: {ticket.status}",
        f"Priority: {ticket.priority}",
    ]

    if extra:
        lines += ["", extra]

    url = _ticket_url(ticket.id)
    if url:
        lines += ["", f"View the ticket: {url}"]

    lines += ["", "- Helix IT Helpdesk"]

    return send_email(
        app,
        recipient.email,
        f"[Ticket #{ticket.id}] {subject}",
        "\n".join(lines),
    )


def _excerpt(text, limit=500):
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def notify_ticket_created(app, ticket, owner):
    return _notify(
        app, owner, None,
        "We received your ticket",
        "Thanks for contacting IT. Your ticket has been created and our "
        "team will be in touch.",
        ticket,
    )


def notify_new_comment(app, ticket, comment, author):
    """A reply was posted: tell the other side of the conversation."""
    is_staff = author.role in ("admin", "agent")

    if is_staff:
        recipient = ticket.user
        subject = "New reply from IT support"
        intro = f"{author.name} replied to your ticket."
    else:
        recipient = ticket.assigned_agent
        subject = "Customer replied"
        intro = f"{author.name} replied to a ticket assigned to you."

    return _notify(
        app, recipient, author, subject, intro, ticket,
        extra=f'Message:\n"{_excerpt(comment.body)}"',
    )


def notify_status_changed(app, ticket, old_status, actor):
    if old_status == ticket.status:
        return False

    return _notify(
        app, ticket.user, actor,
        f"Status changed to {ticket.status}",
        f"The status of your ticket changed from {old_status} "
        f"to {ticket.status}.",
        ticket,
    )


def notify_ticket_assigned(app, ticket, agent, actor):
    return _notify(
        app, agent, actor,
        "Ticket assigned to you",
        "A ticket has been assigned to you.",
        ticket,
    )
