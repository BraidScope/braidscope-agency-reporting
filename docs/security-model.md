# Security model

What Braidscope protects, how, and what a reviewer checks. Short on purpose.

## Who is trusted

| Actor | Trust |
|---|---|
| Browser (signed-in user) | Only what row level security lets that user read. Never holds a platform token or the service role key. |
| Backend (`py/`) | Holds the service role key and the connector tokens. Every route verifies the user's JWT and the user's role on the client workspace. |
| Platforms (Google, Meta, HubSpot, Klaviyo, Shopify) | Read with the user's OAuth grant. Treated as untrusted input: responses are parsed, never executed. |
| AI providers | See numbers the backend already fetched, plus a read-only tool list. Never a credential. |
| Contributors | Change code through pull requests. CI, CODEOWNERS and branch rules decide what merges. |

## Guarantees

1. **Read-only connectors.** No connector writes to a platform. Enforced by `py/app/connectors/policy.py`
   and `py/tests/test_connector_contract.py` on every PR, and by the guarded HTTP client at runtime.
2. **Read-only AI.** Ask AI can only call MCP tools that pass the read-only check (`tool_is_read_only`).
   Third-party MCP servers get an explicit allow-list of those tools; our own servers only expose
   `get_*`, `list_*`, `search` tools; execution refuses any write-like tool name.
3. **Tenant isolation.** Every browser-reachable table has RLS keyed on `auth.uid()` and the
   workspace membership. Backend-only tables have RLS on with no browser policies.
4. **Secrets.** Only in the server's `.env` or GitHub environment secrets. Connector tokens are
   encrypted at rest. The backend refuses to start with missing or malformed secrets. gitleaks scans
   every PR. A secret that ever reached git history is rotated.
5. **Transport.** HTTPS by default, HSTS, security headers on the app, rate limits on sign-in, chat and
   invitations.
6. **Supply chain.** Actions pinned to commit SHAs, Dependabot, `pip-audit` and `npm audit` in CI,
   workflow audit (zizmor), DCO on every commit.

## What a reviewer checks on a connector PR

- The policy diff: every new non-GET call has a purpose that is a read.
- Scopes: read-only by name, or a `scope_note`.
- No token in a log line, an error message, or a response body.
- No new table reachable from the browser without a policy.
- Tests use fixtures, not the network.

## Reporting

Security problems: GitHub private vulnerability reporting on the repository, or the address in
`SECURITY.md`. Never a public issue.
