# AI Chat MCP Setup

Braidscope Ask AI uses the OpenAI Responses API with official MCP servers for
read-only marketing analysis.

## Required environment variables

```env
OPENAI_API_KEY=
OPENAI_CHAT_MODEL=gpt-5
OPENAI_CHAT_MAX_OUTPUT_TOKENS=1200

GOOGLE_ADS_MCP_URL=https://your-google-ads-mcp-domain.example/mcp
GOOGLE_ADS_MCP_ACCESS_TOKEN=
GOOGLE_ADS_CUSTOMER_ID=

KLAVIYO_MCP_URL=https://mcp.klaviyo.com/mcp?read-only=true&disable-tools-with-user-generated-content=true

APP_BASE_URL=http://localhost:8000
WEB_BASE_URL=http://localhost:3000
```

`GOOGLE_ADS_MCP_ACCESS_TOKEN` is a bearer token issued for access to the Google
Ads MCP server. It is not a Google Ads developer token, Google OAuth refresh
token, or OpenAI API key.

Never commit these values or paste them into chat, screenshots, tickets, or
source files.

## Google Ads MCP

The official Google Ads MCP server must be reachable by OpenAI over public
HTTPS. A URL such as `http://localhost:8080/mcp` works with MCP Inspector on the
same computer, but OpenAI cannot reach it.

Deploy the official server to the VPS, configure its Google OAuth credentials,
and complete its MCP OAuth flow. Put the resulting public `/mcp` URL and MCP
bearer token in the Braidscope environment.

Official server:
<https://github.com/googleads/google-ads-mcp>

## Klaviyo MCP

Klaviyo provides a hosted MCP server, so Braidscope does not need to deploy a
separate Klaviyo MCP service. Use the Integrations page and click **Continue
with Klaviyo**. Braidscope uses OAuth dynamic client registration and PKCE,
then keeps the MCP bearer token on the Python backend.

Klaviyo OAuth sessions are short-lived in memory, but connected account tokens
are persisted in Supabase in `connector_oauth_tokens`. The browser never reads
that table; the Python backend reads and writes it with the service-role key
after verifying the selected client.

Official documentation:
<https://developers.klaviyo.com/en/docs/klaviyo_mcp_server>

## HubSpot MCP

HubSpot provides an official remote MCP server at `https://mcp.hubspot.com`.
Create an MCP Auth App in HubSpot, then add the local and production callback
URLs to that app:

```text
http://localhost:8000/py/connectors/hubspot/oauth/callback
https://www.braidscope.com/py/connectors/hubspot/oauth/callback
```

Add these values to the Python environment:

```env
HUBSPOT_MCP_CLIENT_ID=
HUBSPOT_MCP_CLIENT_SECRET=
HUBSPOT_MCP_SCOPES=crm.objects.contacts.read crm.objects.companies.read crm.objects.deals.read
HUBSPOT_MCP_URL=https://mcp.hubspot.com
```

Braidscope stores HubSpot tokens in the same `connector_oauth_tokens` table
using `source_type = hubspot_crm`.

Official documentation:
<https://developers.hubspot.com/docs/apps/developer-platform/build-apps/integrate-with-hubspot-mcp-server>

## Local verification

For a Docker-free local run:

```powershell
cd py
python -m uvicorn app.main:app --reload --port 8000

cd ..\web
npm run dev
```

Open `http://localhost:3000`, sign in, select a client, connect Klaviyo, and
open Ask AI. The readiness banner reports which integrations are usable without
exposing secret values.

The backend verifies the selected client and its connected sources through
Supabase RLS before exposing MCP tools to OpenAI.

## Production follow-up

Apply `supabase/migrations/20260605_001_connector_oauth_tokens.sql` before
deploying Klaviyo OAuth. Do not store OAuth tokens in `data_sources.config`,
browser storage, or React state.
