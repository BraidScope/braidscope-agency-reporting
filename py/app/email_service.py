# SPDX-License-Identifier: AGPL-3.0-only
"""Product notification email delivery — Phase 3 of notification_improvements.md.

Deliberately separate from Supabase Auth's own email sending (signup
confirmation, password reset, invite emails all stay on Supabase Auth — see
py/app/account_settings.py's use of the Auth admin API for invites). Mixing
product notification email into that system was explicitly ruled out by the
spec, so this module owns a completely independent transactional send path.

Provider: Resend, chosen because it's a plain HTTPS/JSON API — no new
dependency beyond the httpx this codebase already uses everywhere else for
service-role calls. Swapping providers later only means changing this one
file; nothing else in the codebase talks to Resend directly.

Configuration is entirely via environment variables (RESEND_API_KEY,
optional EMAIL_FROM_ADDRESS) — never touched by the frontend, never passed
through any API response. Mirrors the existing
supabase_admin.supabase_admin_configured() pattern: every function here is a
no-op (not an exception) when unconfigured, so a deployment without a Resend
key just silently skips email — in-app notifications are unaffected either
way, matching "an email problem must never break the real action."
"""
import base64
import os

import httpx

from .core import logger

RESEND_API_URL = "https://api.resend.com/emails"


def email_configured() -> bool:
    return bool(os.getenv("RESEND_API_KEY"))


def _from_address() -> str:
    return os.getenv("EMAIL_FROM_ADDRESS") or "Braidscope <notifications@braidscope.cloud>"


def _render_html(body_text: str, action_url: str | None, action_label: str | None) -> str:
    safe_body = (body_text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    html = f'<p style="font:14px/1.5 -apple-system,sans-serif;color:#12120f">{safe_body}</p>'
    if action_url and action_label:
        full_url = action_url if action_url.startswith("http") else f"{(os.getenv('WEB_APP_URL') or os.getenv('APP_PUBLIC_URL') or '').rstrip('/')}{action_url}"
        safe_label = action_label.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        html += f'<p style="margin-top:16px"><a href="{full_url}" style="font:600 14px -apple-system,sans-serif;color:#111111">{safe_label} &rarr;</a></p>'
    return html


async def send_notification_email(
    to_email: str,
    subject: str,
    body_text: str,
    action_url: str | None = None,
    action_label: str | None = None,
) -> bool:
    """Best-effort transactional send. Returns True only on a confirmed 2xx
    from the provider; False (never an exception) for every other outcome —
    unconfigured, no recipient, network failure, or a provider-side error.
    Called only after the paired in-app notification has already been
    created, so a failure here never loses the notification itself.

    Retries once on a connection-level error (DNS resolution, refused
    connection, etc.) — this dev network has a characterized intermittent
    "getaddrinfo failed" flake that has affected both Resend and Supabase
    calls all session and always succeeds on a bare retry. Without this, a
    single transient hiccup silently drops the email with no visible error
    (by design — an email problem must never break the actual invitation/
    notification), which is exactly what happened here."""
    if not email_configured():
        return False
    if not to_email:
        return False

    payload = {
        "from": _from_address(),
        "to": [to_email],
        "subject": subject,
        "text": body_text or subject,
        "html": _render_html(body_text or subject, action_url, action_label),
    }
    headers = {
        "Authorization": f"Bearer {os.getenv('RESEND_API_KEY')}",
        "Content-Type": "application/json",
    }

    for attempt in (1, 2):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(RESEND_API_URL, headers=headers, json=payload)
            if response.status_code >= 400:
                logger.warning(
                    "Resend POST failed: url=%s status=%s body=%s payload=%s",
                    RESEND_API_URL,
                    response.status_code,
                    response.text,
                    payload,
                )
                return False
            logger.info(
                "Resend POST accepted: url=%s status=%s body=%s",
                RESEND_API_URL,
                response.status_code,
                response.text,
            )
            return True
        except httpx.TransportError:
            if attempt == 1:
                logger.warning("send_notification_email: transient connection error, retrying once")
                continue
            logger.exception("send_notification_email failed after retry")
            return False
        except Exception:
            logger.exception("send_notification_email raised unexpectedly")
            return False
    return False


async def send_client_invitation_email(
    to_email: str,
    agency_name: str,
    client_name: str,
    role: str,
    accept_url: str,
) -> bool:
    """Client Workspace sharing invitation - reuses the same send path as
    send_notification_email (one Resend integration, not a second one)."""
    role_label = role[0].upper() + role[1:]
    body = (
        f"{agency_name} has given you access to {client_name}.\n\n"
        f"Your role: {role_label}"
    )
    return await send_notification_email(
        to_email,
        "You have been invited to access a Client Workspace",
        body,
        accept_url,
        "Accept Invitation",
    )


async def send_email_with_attachment(
    to_email: str,
    subject: str,
    body_text: str,
    attachment_name: str,
    attachment_bytes: bytes,
    action_url: str | None = None,
    action_label: str | None = None,
) -> bool:
    """Same Resend integration as send_notification_email, plus one file
    attachment (used to deliver a report PDF to a teammate).

    Best-effort like its sibling: returns True only on a confirmed 2xx and False
    for everything else (unconfigured, no recipient, network or provider error);
    it never raises, and never logs the attachment contents."""
    if not email_configured() or not to_email or not attachment_bytes:
        return False

    payload = {
        "from": _from_address(),
        "to": [to_email],
        "subject": subject,
        "text": body_text or subject,
        "html": _render_html(body_text or subject, action_url, action_label),
        "attachments": [{"filename": attachment_name, "content": base64.b64encode(attachment_bytes).decode("ascii")}],
    }
    headers = {
        "Authorization": f"Bearer {os.getenv('RESEND_API_KEY')}",
        "Content-Type": "application/json",
    }
    for attempt in (1, 2):
        try:
            async with httpx.AsyncClient(timeout=45) as client:
                response = await client.post(RESEND_API_URL, headers=headers, json=payload)
            if response.status_code >= 400:
                logger.warning("Resend attachment POST failed: status=%s body=%s", response.status_code, response.text[:300])
                return False
            return True
        except httpx.TransportError:
            if attempt == 1:
                logger.warning("send_email_with_attachment: transient connection error, retrying once")
                continue
            logger.exception("send_email_with_attachment failed after retry")
            return False
        except Exception:
            logger.exception("send_email_with_attachment raised unexpectedly")
            return False
    return False
