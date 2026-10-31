# Security policy

## Reporting a problem

Please do not open a public issue for a security problem.

Report it through GitHub's private vulnerability reporting on this repository
(Security tab, "Report a vulnerability"), or email security@braidscope.com.

We reply within 3 working days with what we will fix and when. We credit reporters in the
release notes unless they prefer not to be named.

## Supported versions

The latest release and the `main` branch.

## Scope

In scope: this application (web, backend, database policies, deploy scripts), the hosted service
at braidscope.cloud, and the self-host package under `deploy/selfhost/`.

Out of scope: third-party services we connect to (Google, Meta, HubSpot, Klaviyo, Shopify,
Supabase, OpenRouter), denial of service, and findings that need a compromised device or account.

## What we do on our side

- Every change goes through a pull request with review and automated checks (lint, tests, build,
  secret scanning, workflow audit).
- Secrets live only in environment files on the server and in GitHub environment secrets, never in
  the repository. A secret that ever reached git history is rotated.
- The backend refuses to start with missing or malformed secrets.
- Row level security protects every table that the browser can reach. Backend-only tables have no
  browser policies at all.
- Connector tokens are encrypted at rest. Report PDFs are private and served only after an
  authorization check.
- Self-host installs get random secrets per install and HTTPS by default on a real domain.

## Self-hosters

Keep the server patched and behind a firewall that allows only 22, 80 and 443. Keep `.env` at mode
600. Run `backup.sh` nightly. The app shows a red banner when served over plain http.
