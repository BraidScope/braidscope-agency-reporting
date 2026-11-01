# SPDX-License-Identifier: AGPL-3.0-only
import os
import time
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, StreamingResponse

from .auth import verify_bearer_token
from .connectors.registry import connector_routers
from .byok import disconnect_byok_provider, get_byok_connection_status, save_byok_key
from .codex import (
    disconnect_codex,
    finalize_codex_login,
    codex_models_loading,
    get_codex_connection_status,
    refresh_codex_models,
    start_codex_login,
)
from .claude_code import (
    disconnect_claude_code,
    get_claude_connection_status,
    refresh_claude_models,
    start_claude_login,
    submit_claude_login_code,
)
from .chat_service import (
    build_mcp_tools,
    get_ai_provider_config,
    get_chat_configuration,
    list_ai_provider_models,
    list_ai_provider_models_for_key,
    stream_chat_response,
)
from .client_context import load_client_context, verify_client_access
from .core import debug_log, logger
from .dashboard_cache import get_or_refresh_dashboard_cache
from .supabase_admin import check_client_reminders, delete_connector_token, get_membership, mark_data_source_disconnected, notify_branding_published, notify_role_changed
from .admin_workspace import list_admin_clients
from .client_access import (
    accept_invitation,
    assert_client_role,
    cancel_invitation,
    change_client_member_role,
    create_client_invitation,
    get_client_team,
    list_client_invitations,
    lookup_invitation,
    resend_invitation,
    remove_client_member,
)
from .account_settings import (
    count_unread_notifications,
    create_account_security_notification,
    get_agency_notification_preferences,
    get_client_notification_preferences,
    invite_member,
    list_notifications,
    mark_notifications_read,
    recent_sessions,
    record_session_activity,
    remove_member,
    save_agency_notification_preferences,
    save_client_notification_preferences,
)
from .google_ads_mcp import router as google_ads_mcp_router
from .ga4_mcp import router as ga4_mcp_router
from .meta_ads_mcp import router as meta_ads_mcp_router
from .reports import router as reports_router
from .google_ads import (
    complete_google_ads_oauth,
    get_google_ads_connection_status,
    google_ads_authorization_url,
    google_ads_configured,
    google_ads_redirect_uri,
    google_ads_web_redirect_url,
    start_google_ads_oauth_session,
)
from .ga4_dashboard import build_ga4_dashboard, resolve_date_window as resolve_ga4_date_window
from .google_ads_dashboard import build_google_ads_dashboard
from .hubspot_dashboard import build_hubspot_dashboard
from .klaviyo_dashboard import build_klaviyo_dashboard
from .meta_ads_dashboard import build_meta_ads_dashboard
from .ga4 import (
    complete_ga4_oauth,
    ga4_authorization_url,
    ga4_configured,
    ga4_redirect_uri,
    ga4_web_redirect_url,
    get_ga4_connection_status,
    start_ga4_oauth_session,
)
from .meta_ads import (
    complete_meta_ads_oauth,
    get_meta_ads_connection_status,
    meta_ads_authorization_url,
    meta_ads_configured,
    meta_ads_redirect_uri,
    meta_ads_web_redirect_url,
    start_meta_ads_oauth_session,
)
from .hubspot import (
    complete_hubspot_oauth,
    get_hubspot_connection_status,
    hubspot_authorization_url,
    hubspot_configured,
    hubspot_redirect_uri,
    hubspot_web_redirect_url,
    maybe_get_direct_hubspot_result,
    start_hubspot_oauth_session,
)
from .klaviyo import (
    complete_klaviyo_oauth,
    get_klaviyo_connection_status,
    klaviyo_authorization_url,
    klaviyo_redirect_uri,
    maybe_get_direct_klaviyo_result,
    register_klaviyo_oauth_client,
    start_klaviyo_oauth_session,
    web_redirect_url,
)
from .mcp_orchestrator import maybe_get_orchestrated_mcp_result
from .openrouter import (
    complete_openrouter_oauth,
    disconnect_openrouter,
    get_openrouter_connection_status,
    openrouter_authorization_url,
    openrouter_web_redirect_url,
    start_openrouter_oauth_session,
)
from .schemas import (
    AIDisconnectRequest,
    AIModelPreviewRequest,
    AuthUser,
    BYOKSaveRequest,
    ChatRequest,
    ClaudeCodeSubmitRequest,
    ConnectorContract,
    ConnectorField,
    ConnectorSyncRequest,
    ConnectorSyncResponse,
    DashboardSummary,
    DataDisconnectRequest,
    MetricCard,
    OAuthStartRequest,
    ShopifyOAuthStartResponse,
    SessionActivityRequest,
    TeamInviteRequest,
)
from .shopify_dashboard import build_shopify_dashboard
from .rate_limit import rate_limit_middleware
from .startup_checks import run_startup_checks
from .shopify import (
    SHOPIFY_SCOPES,
    assert_client_access,
    build_shopify_cards,
    complete_shopify_oauth,
    get_shopify_daily_metrics,
    shopify_authorization_start,
    sync_shopify_orders,
)

def _web_base_url() -> str:
    return os.getenv("WEB_BASE_URL", os.getenv("WEB_APP_URL", os.getenv("APP_PUBLIC_URL", "http://localhost:3000"))).rstrip("/")


app = FastAPI(title="braidscope-py")


@app.on_event("startup")
def _startup_checks() -> None:
    run_startup_checks()


# Per-IP limits on Ask AI, connector sign-in starts and invitations (py/app/rate_limit.py).
app.middleware("http")(rate_limit_middleware)

# The browser reaches this service through Caddy on the same origin, so CORS
# is normally never exercised. Naming the one allowed origin explicitly stops
# a misconfigured proxy from turning this into an open API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_web_base_url()],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
app.include_router(google_ads_mcp_router)
app.include_router(ga4_mcp_router)
app.include_router(meta_ads_mcp_router)
app.include_router(reports_router)
# Connector packages under app/connectors/<id>/ add their own routes here.
for _connector_router in connector_routers():
    app.include_router(_connector_router)



def _is_onboarding_oauth(session: dict | None = None, state: str | None = None) -> bool:
    return session.get("oauth_context") == "onboarding" if session else bool(state and state.startswith("onb."))


def _oauth_callback_url(source_type: str, status_value: str, message: str | None = None) -> str:
    params = {
        "source": source_type,
        "status": status_value,
    }
    if message:
        params["message"] = message
    return f"{_web_base_url()}/oauth/callback?{urlencode(params)}"


def _oauth_redirect(
    source_type: str,
    status_value: str,
    legacy_url: str,
    message: str | None = None,
    session: dict | None = None,
    state: str | None = None,
) -> RedirectResponse:
    if _is_onboarding_oauth(session=session, state=state):
        return RedirectResponse(_oauth_callback_url(source_type, status_value, message))
    return RedirectResponse(legacy_url)


# Baked into the image at build time (Dockerfile ARG, docker-compose build
# args). Lets anyone compare what is live against origin/main without server
# access.
GIT_COMMIT = os.getenv("GIT_COMMIT", "unknown")


@app.get("/py/health")
@app.get("/api/health")  # kept after the Node api service was removed (BS-244)
def health():
    return {
        "service": "py",
        "status": "ok",
        "commit": GIT_COMMIT,
        "ts": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/py/health-auth")
def health_auth(user: AuthUser = Depends(verify_bearer_token)):
    return {
        "service": "py",
        "status": "ok",
        "commit": GIT_COMMIT,
        "user_id": user.sub,
        "ts": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/py/admin/clients")
async def admin_clients(user: AuthUser = Depends(verify_bearer_token)):
    return await list_admin_clients(user.sub)


@app.post("/py/settings/team/invite")
async def settings_team_invite(payload: TeamInviteRequest, user: AuthUser = Depends(verify_bearer_token)):
    return await invite_member(payload.organization_id, payload.email, payload.role, user.sub)

@app.post("/py/settings/team/remove")
async def settings_team_remove(payload: dict[str, str], user: AuthUser = Depends(verify_bearer_token)):
    return await remove_member(payload["organization_id"], payload["user_id"], user.sub)

@app.post("/py/settings/session-activity")
async def settings_session_activity(payload: SessionActivityRequest, request: Request, user: AuthUser = Depends(verify_bearer_token)):
    result = await record_session_activity(user.sub, user.session_id, payload.organization_id, request.headers.get("user-agent", "Unknown device"), request.client.host if request.client else None, payload.client_session_id)
    return {"ok": True, **(result or {})}

@app.get("/py/settings/sessions")
async def settings_sessions(x_braidscope_session_id: str | None = Header(default=None), user: AuthUser = Depends(verify_bearer_token)):
    return {"sessions": await recent_sessions(user.sub, user.session_id, x_braidscope_session_id)}

@app.get("/py/settings/notifications")
async def settings_notifications(
    organization_id: str | None = Query(default=None),
    client_id: str | None = Query(default=None),
    category: str | None = Query(default=None),
    unread_only: bool = Query(default=False),
    before: str | None = Query(default=None),
    limit: int = Query(default=25),
    scope: str = Query(default="client"),
    user: AuthUser = Depends(verify_bearer_token),
):
    if scope not in {"client", "agency"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid notification scope")
    if scope == "agency":
        if not organization_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="organization_id is required")
        if client_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Agency notifications cannot include a client")
        if not await get_membership(user.sub, organization_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member of this organization")
    if client_id and client_id.startswith("demo-"):
        return {"notifications": []}
    known_roles = None
    if client_id:
        # The role resolved here is handed to list_notifications so its access
        # filter doesn't look up the identical role a second time.
        known_roles = {client_id: await assert_client_role(client_id, user.sub, "viewer")}
    return {"notifications": await list_notifications(user.sub, organization_id, client_id, category, unread_only, before, limit, scope, known_roles=known_roles)}

@app.get("/py/settings/notifications/unread-count")
async def settings_notifications_unread_count(
    organization_id: str | None = Query(default=None),
    client_id: str | None = Query(default=None),
    scope: str = Query(default="client"),
    user: AuthUser = Depends(verify_bearer_token),
):
    if scope not in {"client", "agency"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid notification scope")
    if scope == "agency":
        if not organization_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="organization_id is required")
        if client_id or not await get_membership(user.sub, organization_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid agency notification context")
    if client_id and client_id.startswith("demo-"):
        return {"count": 0}
    if client_id:
        await assert_client_role(client_id, user.sub, "viewer")
    return {"count": await count_unread_notifications(user.sub, organization_id, client_id, scope)}

@app.post("/py/settings/notifications/read")
async def settings_notifications_read(payload: dict, user: AuthUser = Depends(verify_bearer_token)):
    await mark_notifications_read(user.sub, payload.get("ids"))
    return {"ok": True}

@app.post("/py/account/security-notification")
async def account_security_notification(payload: dict, user: AuthUser = Depends(verify_bearer_token)):
    event_type = payload.get("event_type")
    if not event_type:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="event_type is required")
    return await create_account_security_notification(user.sub, payload.get("organization_id"), event_type)

@app.get("/py/clients/{client_id}/notification-preferences")
async def client_notification_preferences(client_id: str, user: AuthUser = Depends(verify_bearer_token)):
    return await get_client_notification_preferences(client_id, user.sub)

@app.put("/py/clients/{client_id}/notification-preferences")
async def client_notification_preferences_save(client_id: str, payload: dict, user: AuthUser = Depends(verify_bearer_token)):
    return await save_client_notification_preferences(client_id, user.sub, payload.get("prefs") or payload)

@app.get("/py/organizations/{organization_id}/notification-preferences")
async def agency_notification_preferences(organization_id: str, user: AuthUser = Depends(verify_bearer_token)):
    return await get_agency_notification_preferences(organization_id, user.sub)

@app.put("/py/organizations/{organization_id}/notification-preferences")
async def agency_notification_preferences_save(organization_id: str, payload: dict, user: AuthUser = Depends(verify_bearer_token)):
    return await save_agency_notification_preferences(organization_id, user.sub, payload.get("prefs") or payload)

@app.post("/py/branding/notify-published")
async def branding_notify_published(payload: dict, user: AuthUser = Depends(verify_bearer_token)):
    organization_id = payload.get("organization_id")
    if not organization_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="organization_id is required")
    try:
        result = await notify_branding_published(user.sub, organization_id, payload.get("client_id"), payload.get("publish_key"))
    except HTTPException:
        raise
    except httpx.HTTPStatusError as exc:
        logger.exception("Branding notification backend HTTP failure: status=%s body=%s", exc.response.status_code, exc.response.text)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Branding notification backend failed: {exc.response.text}") from exc
    except httpx.HTTPError as exc:
        logger.exception("Branding notification backend transport failure")
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Branding notification backend failed: {exc}") from exc
    except Exception as exc:
        logger.exception("Branding notification backend failure")
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Branding notification backend failed") from exc
    return {"ok": True, "notification": result}

@app.post("/py/reminders/check")
async def reminders_check(payload: dict, user: AuthUser = Depends(verify_bearer_token)):
    organization_id = payload.get("organization_id")
    client_id = payload.get("client_id")
    if not organization_id or not client_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="organization_id and client_id are required")
    if client_id.startswith("demo-"):
        return {"ok": True, "skipped": True}
    await check_client_reminders(user.sub, organization_id, client_id)
    return {"ok": True}

@app.post("/py/account/notify-role-changed")
async def account_notify_role_changed(payload: dict, user: AuthUser = Depends(verify_bearer_token)):
    organization_id = payload.get("organization_id")
    target_user_id = payload.get("user_id")
    role = payload.get("role")
    if not organization_id or not target_user_id or not role:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="organization_id, user_id and role are required")
    await notify_role_changed(user.sub, organization_id, target_user_id, role)
    return {"ok": True}

@app.get("/py/clients/{client_id}/team")
async def client_team(client_id: str, user: AuthUser = Depends(verify_bearer_token)):
    return {"members": await get_client_team(client_id, user.sub)}

@app.post("/py/clients/{client_id}/invitations")
async def client_create_invitation(client_id: str, payload: dict, user: AuthUser = Depends(verify_bearer_token)):
    email = payload.get("email")
    role = payload.get("role")
    if not email or not role:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="email and role are required")
    return await create_client_invitation(client_id, email, role, user.sub)

@app.get("/py/clients/{client_id}/invitations")
async def client_list_invitations(client_id: str, user: AuthUser = Depends(verify_bearer_token)):
    return {"invitations": await list_client_invitations(client_id, user.sub)}

@app.post("/py/invitations/{invitation_id}/cancel")
async def invitation_cancel(invitation_id: str, user: AuthUser = Depends(verify_bearer_token)):
    await cancel_invitation(invitation_id, user.sub)
    return {"ok": True}

@app.post("/py/invitations/{invitation_id}/resend")
async def invitation_resend(invitation_id: str, user: AuthUser = Depends(verify_bearer_token)):
    return await resend_invitation(invitation_id, user.sub)

@app.get("/py/invitations/lookup")
async def invitation_lookup(token: str = Query(...)):
    return await lookup_invitation(token)

@app.post("/py/invitations/accept")
async def invitation_accept(payload: dict, user: AuthUser = Depends(verify_bearer_token)):
    token = payload.get("token")
    if not token:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="token is required")
    return await accept_invitation(token, user.sub)

@app.delete("/py/clients/{client_id}/team/{target_user_id}")
async def client_remove_member(client_id: str, target_user_id: str, user: AuthUser = Depends(verify_bearer_token)):
    await remove_client_member(client_id, target_user_id, user.sub)
    return {"ok": True}

@app.post("/py/clients/{client_id}/team/{target_user_id}/role")
async def client_change_member_role(client_id: str, target_user_id: str, payload: dict, user: AuthUser = Depends(verify_bearer_token)):
    role = payload.get("role")
    if not role:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="role is required")
    await change_client_member_role(client_id, target_user_id, role, user.sub)
    return {"ok": True}


@app.post("/py/connectors/klaviyo/oauth/start")
async def klaviyo_oauth_start(
    request: OAuthStartRequest,
    user: AuthUser = Depends(verify_bearer_token),
):
    context_request = ChatRequest(
        question="oauth start",
        client_id=request.client_id,
        client_name=request.client_name,
        connected_sources=[],
    )
    await load_client_context(context_request, user)
    await assert_client_role(request.client_id, user.sub, "editor")

    redirect_uri = klaviyo_redirect_uri()
    try:
        oauth_client_id = await register_klaviyo_oauth_client(redirect_uri)
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not register with Klaviyo MCP OAuth",
        ) from exc

    state = start_klaviyo_oauth_session(
        user_id=user.sub,
        client_id=request.client_id,
        oauth_client_id=oauth_client_id,
        redirect_uri=redirect_uri,
        oauth_context=request.oauth_context,
    )
    return {
        "authorization_url": klaviyo_authorization_url(
            oauth_client_id,
            redirect_uri,
            state,
        )
    }


@app.get("/py/connectors/klaviyo/oauth/callback")
async def klaviyo_oauth_callback(
    state: str | None = None,
    code: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
):
    if error:
        message = error_description or error
        return _oauth_redirect("klaviyo", "error", web_redirect_url("error", message), message, state=state)
    if not state or not code:
        message = "Missing OAuth callback parameters"
        return _oauth_redirect("klaviyo", "error", web_redirect_url("error", message), message, state=state)

    try:
        oauth_session = await complete_klaviyo_oauth(state, code)
    except ValueError as exc:
        message = str(exc)
        return _oauth_redirect("klaviyo", "error", web_redirect_url("error", message), message, state=state)
    except (httpx.HTTPError, KeyError) as exc:
        message = f"Klaviyo OAuth failed: {type(exc).__name__}"
        return _oauth_redirect("klaviyo", "error", web_redirect_url("error", message), message, state=state)

    return _oauth_redirect("klaviyo", "connected", web_redirect_url("connected"), session=oauth_session)


@app.get("/py/connectors/klaviyo/status")
async def klaviyo_connection_status(
    client_id: str,
    client_name: str | None = None,
    user: AuthUser = Depends(verify_bearer_token),
):
    context_request = ChatRequest(
        question="connection status",
        client_id=client_id,
        client_name=client_name,
        connected_sources=[],
    )
    await verify_client_access(context_request, user)
    return await get_klaviyo_connection_status(client_id)


@app.post("/py/connectors/hubspot/oauth/start")
async def hubspot_oauth_start(
    request: OAuthStartRequest,
    user: AuthUser = Depends(verify_bearer_token),
):
    if not hubspot_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="HubSpot MCP OAuth is not configured",
        )

    context_request = ChatRequest(
        question="oauth start",
        client_id=request.client_id,
        client_name=request.client_name,
        connected_sources=[],
    )
    await load_client_context(context_request, user)
    await assert_client_role(request.client_id, user.sub, "editor")

    redirect_uri = hubspot_redirect_uri()
    state = start_hubspot_oauth_session(
        user_id=user.sub,
        client_id=request.client_id,
        redirect_uri=redirect_uri,
        oauth_context=request.oauth_context,
    )
    return {"authorization_url": hubspot_authorization_url(redirect_uri, state)}


@app.get("/py/connectors/hubspot/oauth/callback")
async def hubspot_oauth_callback(
    state: str | None = None,
    code: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
):
    if error:
        message = error_description or error
        return _oauth_redirect("hubspot_crm", "error", hubspot_web_redirect_url("error", message), message, state=state)
    if not state or not code:
        message = "Missing OAuth callback parameters"
        return _oauth_redirect("hubspot_crm", "error", hubspot_web_redirect_url("error", message), message, state=state)

    try:
        oauth_session = await complete_hubspot_oauth(state, code)
    except ValueError as exc:
        message = str(exc)
        return _oauth_redirect("hubspot_crm", "error", hubspot_web_redirect_url("error", message), message, state=state)
    except (httpx.HTTPError, KeyError) as exc:
        message = f"HubSpot OAuth failed: {type(exc).__name__}"
        return _oauth_redirect("hubspot_crm", "error", hubspot_web_redirect_url("error", message), message, state=state)

    return _oauth_redirect("hubspot_crm", "connected", hubspot_web_redirect_url("connected"), session=oauth_session)


@app.get("/py/connectors/hubspot/status")
async def hubspot_connection_status(
    client_id: str,
    client_name: str | None = None,
    user: AuthUser = Depends(verify_bearer_token),
):
    context_request = ChatRequest(
        question="connection status",
        client_id=client_id,
        client_name=client_name,
        connected_sources=[],
    )
    await verify_client_access(context_request, user)
    return await get_hubspot_connection_status(client_id)


@app.post("/py/connectors/google-ads/oauth/start")
async def google_ads_oauth_start(
    request: OAuthStartRequest,
    user: AuthUser = Depends(verify_bearer_token),
):
    if not google_ads_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google Ads MCP OAuth is not configured",
        )

    context_request = ChatRequest(
        question="oauth start",
        client_id=request.client_id,
        client_name=request.client_name,
        connected_sources=[],
    )
    await load_client_context(context_request, user)
    await assert_client_role(request.client_id, user.sub, "editor")

    redirect_uri = google_ads_redirect_uri()
    state = start_google_ads_oauth_session(
        user_id=user.sub,
        client_id=request.client_id,
        redirect_uri=redirect_uri,
        oauth_context=request.oauth_context,
    )
    return {"authorization_url": google_ads_authorization_url(redirect_uri, state)}


@app.get("/py/connectors/google-ads/oauth/callback")
async def google_ads_oauth_callback(
    state: str | None = None,
    code: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
):
    if error:
        message = error_description or error
        return _oauth_redirect("google_ads", "error", google_ads_web_redirect_url("error", message), message, state=state)
    if not state or not code:
        message = "Missing OAuth callback parameters"
        return _oauth_redirect("google_ads", "error", google_ads_web_redirect_url("error", message), message, state=state)

    try:
        oauth_session = await complete_google_ads_oauth(state, code)
    except ValueError as exc:
        message = str(exc)
        return _oauth_redirect("google_ads", "error", google_ads_web_redirect_url("error", message), message, state=state)
    except (httpx.HTTPError, KeyError) as exc:
        message = f"Google Ads OAuth failed: {type(exc).__name__}"
        return _oauth_redirect("google_ads", "error", google_ads_web_redirect_url("error", message), message, state=state)

    return _oauth_redirect("google_ads", "connected", google_ads_web_redirect_url("connected"), session=oauth_session)


@app.get("/py/connectors/google-ads/status")
async def google_ads_connection_status(
    client_id: str,
    client_name: str | None = None,
    user: AuthUser = Depends(verify_bearer_token),
):
    context_request = ChatRequest(
        question="connection status",
        client_id=client_id,
        client_name=client_name,
        connected_sources=[],
    )
    await verify_client_access(context_request, user)
    return await get_google_ads_connection_status(client_id)


@app.post("/py/connectors/google-analytics-4/oauth/start")
async def ga4_oauth_start(
    request: OAuthStartRequest,
    user: AuthUser = Depends(verify_bearer_token),
):
    if not ga4_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google Analytics 4 MCP OAuth is not configured",
        )

    context_request = ChatRequest(
        question="oauth start",
        client_id=request.client_id,
        client_name=request.client_name,
        connected_sources=[],
    )
    await load_client_context(context_request, user)

    redirect_uri = ga4_redirect_uri()
    state = start_ga4_oauth_session(
        user_id=user.sub,
        client_id=request.client_id,
        redirect_uri=redirect_uri,
        oauth_context=request.oauth_context,
    )
    return {"authorization_url": ga4_authorization_url(redirect_uri, state)}


@app.get("/py/connectors/google-analytics-4/oauth/callback")
async def ga4_oauth_callback(
    state: str | None = None,
    code: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
):
    if error:
        message = error_description or error
        return _oauth_redirect("google_analytics_4", "error", ga4_web_redirect_url("error", message), message, state=state)
    if not state or not code:
        message = "Missing OAuth callback parameters"
        return _oauth_redirect("google_analytics_4", "error", ga4_web_redirect_url("error", message), message, state=state)

    try:
        oauth_session = await complete_ga4_oauth(state, code)
    except ValueError as exc:
        message = str(exc)
        return _oauth_redirect("google_analytics_4", "error", ga4_web_redirect_url("error", message), message, state=state)
    except (httpx.HTTPError, KeyError) as exc:
        message = f"Google Analytics 4 OAuth failed: {type(exc).__name__}"
        return _oauth_redirect("google_analytics_4", "error", ga4_web_redirect_url("error", message), message, state=state)

    return _oauth_redirect("google_analytics_4", "connected", ga4_web_redirect_url("connected"), session=oauth_session)


@app.get("/py/connectors/google-analytics-4/status")
async def ga4_connection_status(
    client_id: str,
    client_name: str | None = None,
    user: AuthUser = Depends(verify_bearer_token),
):
    context_request = ChatRequest(
        question="connection status",
        client_id=client_id,
        client_name=client_name,
        connected_sources=[],
    )
    await verify_client_access(context_request, user)
    return await get_ga4_connection_status(client_id)


@app.get("/py/dashboards/google-analytics-4")
async def ga4_dashboard(
    background_tasks: BackgroundTasks,
    client_id: str,
    client_name: str | None = None,
    # ge=1, not 7: the dashboard's "Today" (1) and "Yesterday" (2) presets
    # were rejected with a 422 here. build_ga4_dashboard already turns any
    # days >= 1 into a real start/end date window (days=1 -> today..today).
    # The other providers keep ge=7 (Google Ads/Meta map days onto fixed
    # 7/14/30/90 presets and would silently fall back to 30 days).
    days: int = Query(30, ge=1, le=365),
    property_id: str | None = None,
    force_refresh: bool = False,
    # Optional custom range (inclusive, YYYY-MM-DD). When both are given they
    # replace the `days` preset; see resolve_date_window for the rules.
    start_date: date | None = None,
    end_date: date | None = None,
    user: AuthUser = Depends(verify_bearer_token),
):
    context_request = ChatRequest(
        question="dashboard",
        client_id=client_id,
        client_name=client_name,
        connected_sources=[],
    )
    await verify_client_access(context_request, user)
    try:
        resolve_ga4_date_window(days, start_date, end_date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    normalized_property = property_id or "auto"
    window = f"{start_date.isoformat()}:{end_date.isoformat()}" if start_date and end_date else str(days)
    cache_key = f"dashboard:ga4:{client_id}:{normalized_property}:{window}"
    # If force_refresh, skip in-memory cache and fetch live from GA4
    if force_refresh:
        return await build_ga4_dashboard(client_id, days=days, property_id=property_id, force_refresh=True, start=start_date, end=end_date)
    return await get_or_refresh_dashboard_cache(
        cache_key=cache_key,
        factory=lambda: build_ga4_dashboard(client_id, days=days, property_id=property_id, start=start_date, end=end_date),
        background_tasks=background_tasks,
        ttl_seconds=900,
        stale_seconds=86_400,
        lock_seconds=180,
    )


@app.get("/py/dashboards/google-ads")
async def google_ads_dashboard(
    background_tasks: BackgroundTasks,
    client_id: str,
    client_name: str | None = None,
    days: int = Query(30, ge=7, le=365),
    customer_id: str | None = None,
    force_refresh: bool = False,
    user: AuthUser = Depends(verify_bearer_token),
):
    context_request = ChatRequest(
        question="dashboard",
        client_id=client_id,
        client_name=client_name,
        connected_sources=[],
    )
    await verify_client_access(context_request, user)
    normalized_customer = customer_id or "auto"
    cache_key = f"dashboard:google_ads:{client_id}:{normalized_customer}:{days}"
    return await get_or_refresh_dashboard_cache(
        cache_key=cache_key,
        factory=lambda: build_google_ads_dashboard(client_id, days=days, customer_id=customer_id, force_refresh=force_refresh),
        background_tasks=background_tasks,
        ttl_seconds=900,
        stale_seconds=86_400,
        lock_seconds=180,
    )


@app.get("/py/dashboards/meta-ads")
async def meta_ads_dashboard(
    background_tasks: BackgroundTasks,
    client_id: str,
    client_name: str | None = None,
    days: int = Query(30, ge=7, le=365),
    account_id: str | None = None,
    force_refresh: bool = False,
    user: AuthUser = Depends(verify_bearer_token),
):
    context_request = ChatRequest(
        question="dashboard",
        client_id=client_id,
        client_name=client_name,
        connected_sources=[],
    )
    await verify_client_access(context_request, user)
    normalized_account = account_id or "auto"
    cache_key = f"dashboard:meta_ads:{client_id}:{normalized_account}:{days}"
    return await get_or_refresh_dashboard_cache(
        cache_key=cache_key,
        factory=lambda: build_meta_ads_dashboard(client_id, days=days, account_id=account_id, force_refresh=force_refresh),
        background_tasks=background_tasks,
        ttl_seconds=900,
        stale_seconds=86_400,
        lock_seconds=180,
    )


@app.get("/py/dashboards/hubspot")
async def hubspot_dashboard(
    background_tasks: BackgroundTasks,
    client_id: str,
    client_name: str | None = None,
    days: int = Query(30, ge=7, le=365),
    force_refresh: bool = False,
    user: AuthUser = Depends(verify_bearer_token),
):
    context_request = ChatRequest(
        question="dashboard",
        client_id=client_id,
        client_name=client_name,
        connected_sources=[],
    )
    await verify_client_access(context_request, user)
    cache_key = f"dashboard:hubspot:{client_id}:{days}"
    return await get_or_refresh_dashboard_cache(
        cache_key=cache_key,
        factory=lambda: build_hubspot_dashboard(client_id, days=days, force_refresh=force_refresh),
        background_tasks=background_tasks,
        ttl_seconds=900,
        stale_seconds=86_400,
        lock_seconds=180,
    )


@app.get("/py/dashboards/klaviyo")
async def klaviyo_dashboard(
    background_tasks: BackgroundTasks,
    client_id: str,
    client_name: str | None = None,
    days: int = Query(30, ge=7, le=365),
    force_refresh: bool = False,
    user: AuthUser = Depends(verify_bearer_token),
):
    context_request = ChatRequest(
        question="dashboard",
        client_id=client_id,
        client_name=client_name,
        connected_sources=[],
    )
    await verify_client_access(context_request, user)
    cache_key = f"dashboard:klaviyo:{client_id}:{days}"
    return await get_or_refresh_dashboard_cache(
        cache_key=cache_key,
        factory=lambda: build_klaviyo_dashboard(client_id, days=days, force_refresh=force_refresh),
        background_tasks=background_tasks,
        ttl_seconds=900,
        stale_seconds=86_400,
        lock_seconds=180,
    )


@app.post("/py/connectors/data/disconnect")
async def data_connector_disconnect(
    request: DataDisconnectRequest,
    user: AuthUser = Depends(verify_bearer_token),
):
    ALLOWED_TYPES = {
        "google_ads", "google_analytics_4", "meta_ads", "hubspot_crm", "klaviyo",
    }
    source_type = request.source_type.strip()
    if source_type not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Source type must be one of: {', '.join(sorted(ALLOWED_TYPES))}",
        )

    context_request = ChatRequest(
        question="disconnect data connector",
        client_id=request.client_id,
        client_name=request.client_name,
        connected_sources=[],
    )
    await load_client_context(context_request, user)
    await assert_client_role(request.client_id, user.sub, "editor")

    # Clear in-memory token cache
    from .google_ads import GOOGLE_ADS_MCP_TOKENS
    from .ga4 import GA4_MCP_TOKENS
    from .meta_ads import META_ADS_MCP_TOKENS
    from .hubspot import HUBSPOT_MCP_TOKENS
    from .klaviyo import KLAVIYO_MCP_TOKENS

    token_caches = {
        "google_ads": GOOGLE_ADS_MCP_TOKENS,
        "google_analytics_4": GA4_MCP_TOKENS,
        "meta_ads": META_ADS_MCP_TOKENS,
        "hubspot_crm": HUBSPOT_MCP_TOKENS,
        "klaviyo": KLAVIYO_MCP_TOKENS,
    }
    cache = token_caches.get(source_type)
    if cache is not None:
        cache.pop(request.client_id, None)

    # Remove from database
    await delete_connector_token(request.client_id, source_type)
    await mark_data_source_disconnected(request.client_id, source_type, user_id=user.sub)

    logger.info("Disconnected %s for client %s", source_type, request.client_id)
    return {"connected": False, "source_type": source_type}


@app.post("/py/connectors/meta-ads/oauth/start")
async def meta_ads_oauth_start(
    request: OAuthStartRequest,
    user: AuthUser = Depends(verify_bearer_token),
):
    if not meta_ads_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Meta Ads MCP OAuth is not configured",
        )

    context_request = ChatRequest(
        question="oauth start",
        client_id=request.client_id,
        client_name=request.client_name,
        connected_sources=[],
    )
    await load_client_context(context_request, user)
    await assert_client_role(request.client_id, user.sub, "editor")

    redirect_uri = meta_ads_redirect_uri()
    state = start_meta_ads_oauth_session(
        user_id=user.sub,
        client_id=request.client_id,
        redirect_uri=redirect_uri,
        oauth_context=request.oauth_context,
    )
    return {"authorization_url": meta_ads_authorization_url(redirect_uri, state)}


@app.get("/py/connectors/meta-ads/oauth/callback")
async def meta_ads_oauth_callback(
    state: str | None = None,
    code: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
):
    if error:
        message = error_description or error
        return _oauth_redirect("meta_ads", "error", meta_ads_web_redirect_url("error", message), message, state=state)
    if not state or not code:
        message = "Missing OAuth callback parameters"
        return _oauth_redirect("meta_ads", "error", meta_ads_web_redirect_url("error", message), message, state=state)

    try:
        oauth_session = await complete_meta_ads_oauth(state, code)
    except ValueError as exc:
        message = str(exc)
        return _oauth_redirect("meta_ads", "error", meta_ads_web_redirect_url("error", message), message, state=state)
    except (httpx.HTTPError, KeyError) as exc:
        message = f"Meta Ads OAuth failed: {type(exc).__name__}"
        return _oauth_redirect("meta_ads", "error", meta_ads_web_redirect_url("error", message), message, state=state)

    return _oauth_redirect("meta_ads", "connected", meta_ads_web_redirect_url("connected"), session=oauth_session)


@app.get("/py/connectors/meta-ads/status")
async def meta_ads_connection_status(
    client_id: str,
    client_name: str | None = None,
    user: AuthUser = Depends(verify_bearer_token),
):
    context_request = ChatRequest(
        question="connection status",
        client_id=client_id,
        client_name=client_name,
        connected_sources=[],
    )
    await verify_client_access(context_request, user)
    return await get_meta_ads_connection_status(client_id)
@app.post("/py/connectors/openrouter/oauth/start")
async def openrouter_oauth_start(
    request: OAuthStartRequest,
    user: AuthUser = Depends(verify_bearer_token),
):
    context_request = ChatRequest(
        question="oauth start",
        client_id=request.client_id,
        client_name=request.client_name,
        connected_sources=[],
    )
    await load_client_context(context_request, user)
    await assert_client_role(request.client_id, user.sub, "editor")
    state, _verifier = start_openrouter_oauth_session(
        user_id=user.sub,
        client_id=request.client_id,
        model=request.model,
        oauth_context=request.oauth_context,
    )
    return {"authorization_url": openrouter_authorization_url(state)}


@app.get("/py/connectors/openrouter/oauth/callback")
async def openrouter_oauth_callback(
    state: str | None = None,
    code: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
):
    if error:
        message = error_description or error
        return _oauth_redirect("openrouter_ai", "error", openrouter_web_redirect_url("error", message), message, state=state)
    if not state or not code:
        message = "Missing OAuth callback parameters"
        return _oauth_redirect("openrouter_ai", "error", openrouter_web_redirect_url("error", message), message, state=state)

    try:
        oauth_session = await complete_openrouter_oauth(state, code)
    except ValueError as exc:
        message = str(exc)
        return _oauth_redirect("openrouter_ai", "error", openrouter_web_redirect_url("error", message), message, state=state)
    except (httpx.HTTPError, KeyError) as exc:
        message = f"OpenRouter OAuth failed: {type(exc).__name__}"
        return _oauth_redirect("openrouter_ai", "error", openrouter_web_redirect_url("error", message), message, state=state)

    return _oauth_redirect("openrouter_ai", "connected", openrouter_web_redirect_url("connected"), session=oauth_session)


@app.get("/py/connectors/openrouter/status")
async def openrouter_connection_status(
    client_id: str,
    client_name: str | None = None,
    user: AuthUser = Depends(verify_bearer_token),
):
    context_request = ChatRequest(
        question="connection status",
        client_id=client_id,
        client_name=client_name,
        connected_sources=[],
    )
    await verify_client_access(context_request, user)
    return await get_openrouter_connection_status(client_id)


@app.post("/py/connectors/openai/codex/start")
async def openai_codex_auth_start(
    request: OAuthStartRequest,
    user: AuthUser = Depends(verify_bearer_token),
):
    context_request = ChatRequest(
        question="codex authentication start",
        client_id=request.client_id,
        client_name=request.client_name,
        connected_sources=[],
    )
    await load_client_context(context_request, user)
    await assert_client_role(request.client_id, user.sub, "editor")
    try:
        return await start_codex_login(
            user_id=user.sub,
            client_id=request.client_id,
            model=request.model,
            oauth_context=request.oauth_context,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Could not start the Codex CLI: {exc}",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Codex login failed: {type(exc).__name__}: {exc}",
        ) from exc


async def _background_refresh_codex_models(client_id: str) -> None:
    # Best-effort, fire-and-forget: several real `codex exec` calls (each
    # several seconds), so this must never block the login-status response
    # the frontend is polling every 2s. A failure here just means the picker
    # falls back to its own on-demand discovery the first time it's opened
    # (list_codex_models already handles "no cache yet") - never fatal, so
    # broad exception handling is the right call for a background task that
    # exists purely to warm a cache, not one whose caller can react to it.
    try:
        await refresh_codex_models(client_id)
    except Exception as exc:  # noqa: BLE001 - background cache warm, never fatal
        debug_log("  _background_refresh_codex_models(client_id=%s) failed: %s: %s", client_id, type(exc).__name__, exc)


# Tracks which login ATTEMPTS have already had a models-refresh scheduled,
# purely so the frontend's repeated polling of the status endpoint below
# (getOpenAICodexServerConnection - every ~2s while a login is pending, per
# App.jsx) triggers the real, multi-call, several-second discovery round
# exactly ONCE per fresh connection, not on every single poll tick after the
# connection is already up. Keyed by login_attempt_id, NOT session_id:
# session_id is sha256(user_id:client_id) and is therefore IDENTICAL across
# a reconnect by the same user for the same client, which would silently
# skip the refresh a genuine reconnect (different ChatGPT account/plan,
# e.g.) needs; login_attempt_id is a fresh random value start_codex_login()
# generates on every call, including a reconnect. Deliberately NOT inside
# codex.py / CODEX_SESSIONS: this is scheduling bookkeeping for this one
# endpoint, not authentication state, so finalize_codex_login's own
# existing behavior (which already runs safely on repeat calls) is left
# completely untouched.
_CODEX_MODELS_REFRESH_SCHEDULED: set[str] = set()


@app.get("/py/connectors/openai/codex/status")
async def openai_codex_auth_status(
    background_tasks: BackgroundTasks,
    client_id: str,
    client_name: str | None = None,
    user: AuthUser = Depends(verify_bearer_token),
):
    context_request = ChatRequest(
        question="codex authentication status",
        client_id=client_id,
        client_name=client_name,
        connected_sources=[],
    )
    await verify_client_access(context_request, user)
    result = await get_codex_connection_status(user.sub, client_id)
    session_id = result.get("session_id")
    if session_id and result.get("status") == "connected":
        finalized = await finalize_codex_login(session_id)
        attempt_id = result.get("login_attempt_id") or session_id
        if finalized and attempt_id not in _CODEX_MODELS_REFRESH_SCHEDULED:
            # Phase 6: refresh discovery when a connection is
            # (re-)authenticated, not only ever after that - a reconnect
            # (different ChatGPT account/plan, e.g.) must not keep serving a
            # stale model list or a stale stored default from before.
            _CODEX_MODELS_REFRESH_SCHEDULED.add(attempt_id)
            background_tasks.add_task(_background_refresh_codex_models, client_id)
    return await get_codex_connection_status(user.sub, client_id)


@app.post("/py/connectors/anthropic/claude-code/start")
async def claude_code_auth_start(
    request: OAuthStartRequest,
    user: AuthUser = Depends(verify_bearer_token),
):
    context_request = ChatRequest(
        question="claude code authentication start",
        client_id=request.client_id,
        client_name=request.client_name,
        connected_sources=[],
    )
    await load_client_context(context_request, user)
    await assert_client_role(request.client_id, user.sub, "editor")
    try:
        return await start_claude_login(
            user_id=user.sub,
            client_id=request.client_id,
            oauth_context=request.oauth_context,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Could not start the Claude Code CLI: {type(exc).__name__}",
        ) from exc


@app.post("/py/connectors/anthropic/claude-code/submit-code")
async def claude_code_auth_submit_code(
    request: ClaudeCodeSubmitRequest,
    background_tasks: BackgroundTasks,
    user: AuthUser = Depends(verify_bearer_token),
):
    context_request = ChatRequest(
        question="claude code authentication code",
        client_id=request.client_id,
        client_name=request.client_name,
        connected_sources=[],
    )
    await load_client_context(context_request, user)
    await assert_client_role(request.client_id, user.sub, "editor")
    try:
        result = await submit_claude_login_code(user.sub, request.client_id, request.code)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if result.get("connected"):
        background_tasks.add_task(_background_refresh_claude_models, request.client_id, user.sub)
    return result


async def _background_refresh_claude_models(client_id: str, user_id: str) -> None:
    # Best-effort cache warm (several real, small `claude -p` probes) - never
    # fatal; the model picker falls back to on-demand discovery.
    try:
        await refresh_claude_models(client_id, user_id)
    except Exception as exc:  # noqa: BLE001 - background cache warm
        debug_log("  _background_refresh_claude_models failed: %s", type(exc).__name__)


@app.get("/py/connectors/anthropic/claude-code/status")
async def claude_code_auth_status(
    client_id: str,
    client_name: str | None = None,
    user: AuthUser = Depends(verify_bearer_token),
):
    context_request = ChatRequest(
        question="claude code authentication status",
        client_id=client_id,
        client_name=client_name,
        connected_sources=[],
    )
    await verify_client_access(context_request, user)
    return await get_claude_connection_status(user.sub, client_id)


@app.post("/py/connectors/ai/byok")
async def ai_byok_save(
    request: BYOKSaveRequest,
    user: AuthUser = Depends(verify_bearer_token),
):
    provider = request.provider.strip().lower()
    if provider not in {"openai", "anthropic"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provider must be openai or anthropic",
        )

    context_request = ChatRequest(
        question="save ai provider key",
        client_id=request.client_id,
        client_name=request.client_name,
        connected_sources=[],
    )
    await load_client_context(context_request, user)
    await assert_client_role(request.client_id, user.sub, "editor")

    try:
        saved = await save_byok_key(
            client_id=request.client_id,
            provider=provider,
            api_key=request.api_key,
            model=request.model,
            user_id=user.sub,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return {"connected": True, **saved}


@app.get("/py/connectors/ai/byok/status")
async def ai_byok_status(
    provider: str,
    client_id: str,
    client_name: str | None = None,
    user: AuthUser = Depends(verify_bearer_token),
):
    normalized_provider = provider.strip().lower()
    if normalized_provider not in {"openai", "anthropic"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provider must be openai or anthropic",
        )

    context_request = ChatRequest(
        question="ai provider status",
        client_id=client_id,
        client_name=client_name,
        connected_sources=[],
    )
    await verify_client_access(context_request, user)
    return await get_byok_connection_status(client_id, normalized_provider)


@app.post("/py/connectors/ai/disconnect")
async def ai_provider_disconnect(
    request: AIDisconnectRequest,
    user: AuthUser = Depends(verify_bearer_token),
):
    provider = request.provider.strip().lower()
    if provider not in {"openrouter", "openai", "anthropic", "claude_code"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provider must be openrouter, openai, anthropic, or claude_code",
        )

    context_request = ChatRequest(
        question="disconnect ai provider",
        client_id=request.client_id,
        client_name=request.client_name,
        connected_sources=[],
    )
    await load_client_context(context_request, user)
    await assert_client_role(request.client_id, user.sub, "editor")

    if provider == "openrouter":
        await disconnect_openrouter(request.client_id, user_id=user.sub)
    elif provider == "openai":
        await disconnect_codex(user.sub, request.client_id)
    elif provider == "claude_code":
        await disconnect_claude_code(user.sub, request.client_id)
    else:
        await disconnect_byok_provider(request.client_id, provider, user_id=user.sub)
    return {"connected": False, "provider": provider}


@app.get("/py/connectors/ai/models")
async def ai_provider_models(
    provider: str,
    client_id: str,
    client_name: str | None = None,
    user: AuthUser = Depends(verify_bearer_token),
):
    normalized_provider = provider.strip().lower()
    # "openai_codex" (the ChatGPT/Codex CLI connection) is intentionally its
    # own distinct value here, never merged with "openai" (the separate,
    # still-supported raw-API-key BYOK path) or "openrouter" - each resolves
    # to a completely separate model list in list_ai_provider_models, so a
    # request for one can never return another's models.
    if normalized_provider not in {"openrouter", "openai", "anthropic", "openai_codex", "claude_code"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provider must be openrouter, openai, openai_codex, anthropic, or claude_code",
        )

    context_request = ChatRequest(
        question="list ai models",
        client_id=client_id,
        client_name=client_name,
        connected_sources=[],
    )
    await load_client_context(context_request, user)

    try:
        models = await list_ai_provider_models(client_id, normalized_provider, user_id=user.sub)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not load {normalized_provider} models",
        ) from exc
    payload = {"provider": normalized_provider, "models": models}
    if normalized_provider == "openai_codex":
        # Codex verifies models in the background (each is a real CLI run); this
        # additive flag lets the picker keep asking while that is still going.
        payload["loading"] = await codex_models_loading(client_id, models)
    return payload


@app.post("/py/connectors/ai/models/preview")
async def ai_provider_models_preview(
    request: AIModelPreviewRequest,
    user: AuthUser = Depends(verify_bearer_token),
):
    normalized_provider = request.provider.strip().lower()
    if normalized_provider not in {"openrouter", "openai", "anthropic"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provider must be openrouter, openai, or anthropic",
        )

    context_request = ChatRequest(
        question="preview ai models",
        client_id=request.client_id,
        client_name=request.client_name,
        connected_sources=[],
    )
    await load_client_context(context_request, user)

    try:
        models = await list_ai_provider_models_for_key(
            normalized_provider,
            request.api_key,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not load {normalized_provider} models",
        ) from exc
    return {"provider": normalized_provider, "models": models}


async def resolve_direct_tool_result(
    request: ChatRequest,
    client_context: dict[str, Any],
    tools: list[dict[str, Any]],
    ai_config: dict[str, str],
) -> dict[str, Any] | None:
    """Try, in order, the fast deterministic Klaviyo intent match, the fast
    deterministic HubSpot intent match, then the slower generic MCP
    orchestrator - stopping at the first one that matches.

    Each lookup is wrapped in its OWN try/except rather than one try/except
    around all three. Confirmed live on 2026-09-24: a transient network blip
    (DNS resolution failure) during the FIRST lookup raised an exception
    that used to abort this whole function immediately - so
    maybe_get_direct_hubspot_result and, critically,
    maybe_get_orchestrated_mcp_result (which has its own retry logic via
    _mcp_post_with_retry and is far more resilient to exactly this kind of
    blip) never even got a chance to run. "Show me my Klaviyo account
    details" answered with the generic "No relevant read-only MCP tool
    matched" even though the slower, sturdier orchestrator path would very
    likely have succeeded. A blip in one lookup must not spend the other
    two lookups' chances to still answer the question.
    """
    prefetch_started_at = time.monotonic()

    direct_tool_result = None
    t0 = time.monotonic()
    try:
        direct_tool_result = await maybe_get_direct_klaviyo_result(
            request,
            client_context,
            tools,
        )
    except (httpx.HTTPError, ValueError):
        logger.exception("maybe_get_direct_klaviyo_result failed - falling through to the next lookup")
    debug_log("  maybe_get_direct_klaviyo_result: %.2fs (matched=%s)", time.monotonic() - t0, bool(direct_tool_result))

    if not direct_tool_result:
        t0 = time.monotonic()
        try:
            direct_tool_result = await maybe_get_direct_hubspot_result(
                request,
                client_context,
                tools,
            )
        except (httpx.HTTPError, ValueError):
            logger.exception("maybe_get_direct_hubspot_result failed - falling through to the next lookup")
        debug_log("  maybe_get_direct_hubspot_result: %.2fs (matched=%s)", time.monotonic() - t0, bool(direct_tool_result))

    if not direct_tool_result:
        t0 = time.monotonic()
        try:
            direct_tool_result = await maybe_get_orchestrated_mcp_result(
                request,
                client_context,
                tools,
                ai_config,
            )
        except (httpx.HTTPError, ValueError):
            # A pre-fetch MCP call failing (network blip, a platform's server
            # briefly unreachable, etc.) used to hard-fail the WHOLE Ask AI
            # turn with a raw "MCP call failed: ConnectError" - visible to
            # every user regardless of whether their question even needed
            # that data (the single-connected-platform fallback in
            # mcp_orchestrator.py's fallback_platform() already picks a
            # platform for ANY question, even a bare "hi", once only one is
            # connected). Degrading instead of hard-failing means a transient
            # tool-call failure gets the same honest "ask a specific
            # connected-data question" reply the chat already gives when no
            # MCP tool matched at all, rather than an opaque connection-error
            # box - the failure is still fully logged server-side for real
            # debugging, just not thrown at the user as a broken request.
            logger.exception("maybe_get_orchestrated_mcp_result failed - answering without pre-fetched tool data")
            direct_tool_result = None
        debug_log("  maybe_get_orchestrated_mcp_result: %.2fs (matched=%s)", time.monotonic() - t0, bool(direct_tool_result))

    debug_log("Ask AI MCP pre-fetch phase total: %.2fs", time.monotonic() - prefetch_started_at)
    return direct_tool_result


@app.post("/py/chat")
async def chat(
    request: ChatRequest,
    user: AuthUser = Depends(verify_bearer_token),
):
    if not request.question.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Question is required",
        )

    client_context = await load_client_context(request, user)
    connected_sources = client_context["connected_sources"]
    configuration = await get_chat_configuration(client_context["id"], connected_sources, user_id=user.sub)
    if not configuration["openai_configured"]:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Connect OpenRouter, ChatGPT, or Claude before using Ask AI",
        )

    tools = await build_mcp_tools(client_context["id"], connected_sources)
    if not tools:
        missing = ", ".join(configuration["missing_integrations"])
        detail = (
            f"MCP credentials are not configured for: {missing}"
            if missing
            else "Connect Google Ads, Klaviyo, or HubSpot before using Ask AI"
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=detail,
        )

    ai_config = await get_ai_provider_config(
        client_context["id"],
        preferred_provider=request.ai_provider,
        model_override=request.ai_model,
        user_id=user.sub,
    )
    if not ai_config:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Selected AI provider is not connected for this client",
        )
    debug_log(
        "Ask AI request start client_id=%s provider=%s question=%r",
        client_context["id"], ai_config.get("provider"), request.question[:200],
    )
    direct_tool_result = await resolve_direct_tool_result(request, client_context, tools, ai_config)

    return StreamingResponse(
        stream_chat_response(request, client_context, tools, direct_tool_result, ai_config),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/py/chat/config")
async def chat_config(
    client_id: str,
    client_name: str | None = None,
    connected_sources: str = "",
    user: AuthUser = Depends(verify_bearer_token),
):
    sources = [source.strip() for source in connected_sources.split(",") if source.strip()]
    request = ChatRequest(
        question="configuration check",
        client_id=client_id,
        client_name=client_name,
        connected_sources=sources,
    )
    client_context = await load_client_context(request, user)
    return await get_chat_configuration(
        client_context["id"],
        client_context["connected_sources"],
        user_id=user.sub,
    )


@app.get("/py/connectors/shopify/contract", response_model=ConnectorContract)
def shopify_contract(user: AuthUser = Depends(verify_bearer_token)):
    return ConnectorContract(
        type="shopify",
        name="Shopify",
        mode="api",
        auth_type="oauth",
        status="contract_ready",
        scopes=[*SHOPIFY_SCOPES],
        setup_fields=[
            ConnectorField(name="shop_domain", label="Shop domain", type="text", required=True),
        ],
        sync_entities=[
            "orders",
            "refunds",
            "products",
            "customers",
        ],
        metrics=[
            "revenue",
            "orders",
            "average_order_value",
            "refunds",
            "customers",
        ],
        endpoints={
            "oauth_start": "/py/connectors/shopify/oauth/start",
            "oauth_callback": "/py/connectors/shopify/oauth/callback",
            "sync": "/py/connectors/shopify/sync",
        },
        notes=[
            "Frontend must not store Shopify credentials.",
            "OAuth state and encrypted token storage are handled server-side.",
            "Dashboard data should be read from analytics_shopify_* tables once sync is real.",
            "Use GraphQL Admin API for the real sync worker; REST Admin is legacy for new public apps.",
        ],
    )


@app.get("/py/connectors/shopify/oauth/start", response_model=ShopifyOAuthStartResponse)
async def shopify_oauth_start(
    client_id: str,
    shop: str = Query(..., description="Shopify shop domain or store slug"),
    user: AuthUser = Depends(verify_bearer_token),
):
    await assert_client_access(client_id, user, "editor")
    return await shopify_authorization_start(client_id, shop, user)


@app.get("/py/connectors/shopify/oauth/redirect")
async def shopify_oauth_redirect(
    client_id: str,
    shop: str = Query(..., description="Shopify shop domain or store slug"),
    user: AuthUser = Depends(verify_bearer_token),
):
    start = await shopify_oauth_start(client_id=client_id, shop=shop, user=user)
    return RedirectResponse(start.auth_url, status_code=status.HTTP_302_FOUND)


@app.get("/py/connectors/shopify/oauth/callback")
async def shopify_oauth_callback(request: Request):
    return await complete_shopify_oauth(request)


@app.get("/py/dashboards/shopify")
async def shopify_dashboard(
    background_tasks: BackgroundTasks,
    client_id: str,
    client_name: str | None = None,
    days: int = Query(30, ge=7, le=365),
    force_refresh: bool = False,
    user: AuthUser = Depends(verify_bearer_token),
):
    await assert_client_access(client_id, user)
    cache_key = f"dashboard:shopify:{client_id}:{days}"
    if force_refresh:
        return await build_shopify_dashboard(client_id, days=days, force_refresh=True)
    return await get_or_refresh_dashboard_cache(
        cache_key=cache_key,
        factory=lambda: build_shopify_dashboard(client_id, days=days),
        background_tasks=background_tasks,
        ttl_seconds=900,
        stale_seconds=86_400,
        lock_seconds=180,
    )


@app.post("/py/connectors/shopify/sync", response_model=ConnectorSyncResponse)
async def shopify_sync(
    request: ConnectorSyncRequest,
    user: AuthUser = Depends(verify_bearer_token),
):
    await assert_client_access(request.client_id, user, "editor")
    result = await sync_shopify_orders(request.client_id, request.days)
    return ConnectorSyncResponse(
        type="shopify",
        client_id=request.client_id,
        status="synced",
        message="Shopify orders and daily metrics were synced into analytics tables.",
        synced_entities=["orders", "refunds", "daily_metrics"],
        generated_at=datetime.now(timezone.utc).isoformat(),
        orders_synced=result["orders_synced"],
        daily_rows_synced=result["daily_rows_synced"],
        product_rows_synced=result["product_rows_synced"],
    )


@app.get("/py/dashboard/summary", response_model=DashboardSummary)
async def dashboard_summary(
    client_id: str,
    days: int = 30,
    user: AuthUser = Depends(verify_bearer_token),
):
    await assert_client_access(client_id, user)
    shopify_rows = await get_shopify_daily_metrics(client_id, days)
    if shopify_rows:
        shopify_cards, revenue_by_week, overview_cards = build_shopify_cards(shopify_rows)
    else:
        shopify_cards = [
            MetricCard(label="Orders", value="342", direction="up", detail="+9%"),
            MetricCard(label="AOV", value="$102", direction="up", detail="+3%"),
            MetricCard(label="Revenue", value="$34.8k", direction="up", detail="+11%"),
            MetricCard(label="Conv. rate", value="2.4%", direction="dn", detail="-2%"),
        ]
        revenue_by_week = [60, 72, 85, 95]
        overview_cards = [
            MetricCard(label="Total revenue", value="$35.0k", direction="up", detail="+12% vs last period"),
            MetricCard(label="Ad spend", value="$8.4k", direction="up", detail="+4%"),
            MetricCard(label="Blended ROAS", value="4.2x", direction="up", detail="+8%"),
            MetricCard(label="Orders", value="342", direction="up", detail="+9%"),
        ]

    return DashboardSummary(
        client_id=client_id,
        days=days,
        generated_at=datetime.now(timezone.utc).isoformat(),
        overview=overview_cards,
        google_ads=[
            MetricCard(label="Clicks", value="12.4k", direction="up", detail="+8%"),
            MetricCard(label="CTR", value="3.8%", direction="up", detail="+0.2pt"),
            MetricCard(label="Conversions", value="284", direction="dn", detail="-6%"),
            MetricCard(label="CPA", value="$18.2", direction="dn", detail="+14%"),
        ],
        meta_ads=[
            MetricCard(label="Reach", value="84k", direction="up", detail="+5%"),
            MetricCard(label="CPM", value="$12.4", direction="dn", detail="+22%"),
            MetricCard(label="ROAS", value="3.9x", direction="dn", detail="-7%"),
            MetricCard(label="Frequency", value="3.8", direction="dn", detail="fatigue"),
        ],
        shopify=shopify_cards,
        clicks_by_day=[55, 70, 48, 82, 64, 90, 58],
        revenue_by_week=revenue_by_week,
        top_campaigns=[
            {"campaign": "Summer Sale - Video", "spend": "$1,420", "roas": "6.1x", "revenue": "$8,662"},
            {"campaign": "Retargeting - Cart abandoners", "spend": "$890", "roas": "5.2x", "revenue": "$4,628"},
            {"campaign": "Prospecting - Lookalike 2%", "spend": "$1,210", "roas": "3.4x", "revenue": "$4,114"},
            {"campaign": "Brand awareness - Static", "spend": "$680", "roas": "1.1x", "revenue": "$748"},
        ],
    )
