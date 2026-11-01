# Building a connector

A connector brings one platform's numbers into Braidscope. It reads. It never writes back to the
platform. You can build one without touching the rest of the codebase.

## The rule

**Read-only, enforced, not promised.** Every connector is checked by `py/tests/test_connector_contract.py`
on every pull request:

- Every outbound call that is not a GET must be listed in `py/app/connectors/policy.py` with its purpose
  (an OAuth token exchange, a GraphQL or GAQL query, a search POST, MCP JSON-RPC). Anything else fails CI.
- No GraphQL document may contain a `mutation`.
- The OAuth scopes you request must be the read scopes recorded in the policy.
- New connector packages must use the guarded HTTP client, never `httpx` directly.

Fix the policy in the same PR as the code, so the reviewer sees both.

## What a connector is made of

| Piece | Where | What it does |
|---|---|---|
| Policy | `py/app/connectors/policy.py` | Scopes, allowed hosts, the few non-GET reads |
| Backend package | `py/app/connectors/<id>/` | OAuth start and callback, token storage, fetch, dashboard payload, optional MCP tools for Ask AI |
| Migration (optional) | `supabase/migrations/` | A cache table for synced rows, backend-only, RLS on with no browser policies |
| Web catalog entry | `web/src/lib/data.js` | Name, logo, what the user connects |
| Dashboard tab | `web/src/pages/Dashboards.jsx` | Cards, trends, table from the dashboard payload |

The six existing connectors still live as flat modules in `py/app/` (`shopify.py`, `hubspot.py`, ...).
They follow the same policy; new ones go in the package layout.

## Start one in a minute

```bash
python scripts/new_connector.py linkedin_ads "LinkedIn Ads"
```

This creates `py/app/connectors/linkedin_ads/` with a policy entry, a router with the OAuth routes,
a `fetch.py` using the guarded client, a dashboard builder stub, and a test file. The router is
picked up automatically; `main.py` is not edited.

## The guarded client

```python
from app.connectors.http import connector_client

async with connector_client("linkedin_ads", timeout=30) as client:
    response = await client.get(f"{API}/adAnalytics", headers=auth(token), params=params)
```

Before any request leaves, the client checks the host against the policy, refuses a non-GET that is
not a listed read, and refuses a GraphQL body with a mutation. A violation raises `ReadOnlyViolation`
and nothing is sent.

## Credentials

- Tokens go through `encrypt_token` / `decrypt_token` (Fernet, key from `CONNECTOR_TOKEN_ENCRYPTION_KEY`)
  into `integration_credentials` or `connector_oauth_tokens`, via the backend only.
- The browser never sees a platform token. The backend fetches with the service role and returns
  numbers, not credentials.
- OAuth state is hashed, stored with an expiry, and consumed once.
- Ask for the smallest scope set that reads what the dashboard shows. If the platform has no read-only
  scope (Google Ads), say so in `scope_note` and keep the code to read endpoints.

## Tests

- Fixtures only. No live API calls in tests.
- The contract test runs for you. Run it locally: `cd py && python -m pytest tests/test_connector_contract.py`.

## Pull request checklist

- [ ] Policy entry added or updated, with a purpose for every non-GET call
- [ ] Scopes are read-only, or `scope_note` explains why not
- [ ] No platform token reaches the browser or the logs
- [ ] Fixture-based tests for the fetch and the dashboard payload
- [ ] One connector per PR, signed off (`git commit -s`)
