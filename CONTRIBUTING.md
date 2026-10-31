# Contributing to Braidscope

Thanks for looking. This file is short on purpose.

## Run it locally

One command, needs Docker:

```bash
curl -fsSL https://raw.githubusercontent.com/BraidScope/braidscope-agency-reporting/main/deploy/selfhost/install.sh | bash
```

Or from a clone: `cd deploy/selfhost && cp .env.example .env`, fill the secrets, `docker compose up -d --build`.
Details in `docs/self-host.md`.

## Run the checks

```bash
# Python
cd py && pip install -r requirements.txt
ruff check .
SUPABASE_URL=https://example.supabase.co SUPABASE_SERVICE_ROLE_KEY=x CODEX_CLI_COMMAND=true RATE_LIMIT_DISABLED=1 \
  python -m unittest discover -s tests -p "test_*.py"

# Web
cd web && npm ci && npm test && npm run build
```

CI runs the same plus secret scanning, workflow audit and the DCO check. All must be green.

## Sign your commits (DCO)

Every commit carries a `Signed-off-by: Your Name <you@example.com>` line. Use `git commit -s`.
By signing off you certify the Developer Certificate of Origin 1.1 (developercertificate.org): you wrote
the change, or have the right to submit it, under this project's licence (AGPL-3.0-only).
There is no contributor licence agreement.

## Pull requests

- One topic per PR. Small is good.
- Title: what it does, in plain words. Reference the issue.
- No real keys, tokens, hosts or client names anywhere, including test fixtures and screenshots.
- A new dependency is named in the PR with its licence.
- Docs change with behaviour.

## Connectors

Google Ads, Meta Ads, GA4, HubSpot, Klaviyo and Shopify connectors live in `py/app`. New platforms are
welcome: open a connector request issue first so we agree on the shape, then one connector per PR with
fixtures only, no live API calls in tests. We keep at most three connector PRs in review at a time.

## Where to ask

GitHub Discussions for questions. Issues for bugs and requests. Security problems through the private
report form, never a public issue (see `SECURITY.md`).

## Code of conduct

Contributor Covenant 2.1, in `CODE_OF_CONDUCT.md`.
