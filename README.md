# Braidscope

**Open-source client reporting for marketing agencies.** Every client's ad and analytics numbers in one
place, with your agency's branding. Self-host it in one command, or use our cloud.

> **Status: preparing for public release.**

![Braidscope](web/public/og-image.png)

## Who this is for

- Marketing agencies with 8 to 30 active clients.
- Teams that run Meta Ads, Google Ads, GA4, HubSpot, Klaviyo or Shopify for clients and spend Monday mornings on reports.
- Anyone who wants client reporting on their own server, with their own data.

## What works today

| Area | Status |
|---|---|
| One workspace per client, team invites, roles (owner, admin, editor, viewer) | Live |
| Google Ads, Meta Ads, GA4, HubSpot and Klaviyo dashboards | Live |
| Shopify | Sync only, no dashboard tab yet |
| Date ranges, world map, per-source dashboards | Live |
| Ask AI over connected data: your own API key, OpenRouter, or sign in with Claude or ChatGPT | Live |
| Reports: library, drafts, AI narrative, PDF download | Live |
| Per-client and agency branding | Live |
| Two-factor sign-in, notifications | Live |
| Scheduled report delivery, anomaly alerts, Google Sheets export, client portals | Not built |

## Quick start

One command on a Linux box with Docker:

```bash
curl -fsSL https://raw.githubusercontent.com/BraidScope/braidscope-agency-reporting/main/deploy/selfhost/install.sh | bash
```

It prints the URL and the first owner login. With a real domain, HTTPS turns on by itself:

```bash
BRAIDSCOPE_DOMAIN=app.example.com BRAIDSCOPE_EMAIL=you@example.com \
  bash -c "$(curl -fsSL https://raw.githubusercontent.com/BraidScope/braidscope-agency-reporting/main/deploy/selfhost/install.sh)"
```

Everything else, including upgrade, backup and moving from the cloud, is in [docs/self-host.md](docs/self-host.md).

## Self-host or cloud

| Self-host (this repo, free) | Braidscope Cloud |
|---|---|
| Every connector: Google Ads, Meta Ads, GA4, HubSpot, Klaviyo, Shopify | The same, hosted and kept working when platforms change their APIs |
| Dashboards, reports, PDF download, Ask AI | Backups, upgrades and monitoring done for you |
| Team and roles, client workspaces | White-label domains, priority support |
| You run it on your server | We run it: [braidscope.com](https://braidscope.com) |

White-label and larger plans: contact us through the site.

## How it is built

| Folder | What |
|---|---|
| `web/` | React app (Vite, JSX) |
| `py/` | Python backend (FastAPI): connectors, dashboards, Ask AI, reports |
| `supabase/migrations/` | Database schema and row level security (Postgres with the Supabase extensions) |
| `deploy/selfhost/` | The one-command stack: Postgres, auth, storage, backend, app, HTTPS |
| `docs/` | Install, upgrade, backup, migration |

`AGENTS.md` describes the architecture and the rules in detail.

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md). Short version: run the checks, sign your commits (`git commit -s`),
one topic per PR, no real keys anywhere. Connectors for new platforms are welcome.

## Security

Connectors only read. No connector writes to a platform, and the AI assistant can only call read-only
tools. That is enforced by a policy file and a CI test, not by convention: see
[docs/security-model.md](docs/security-model.md). To add a platform, read
[docs/connectors.md](docs/connectors.md); a scaffold command gets you a working package in a minute.

## Licence

AGPL-3.0-only. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

In plain words: you can run it, change it and share it. If you offer a modified Braidscope to others
over a network, you must publish your changes under the same licence. If that does not fit your
business, a commercial licence is available: hello@braidscope.com. The name and logo are trademarks,
see [TRADEMARK.md](TRADEMARK.md). Security problems: see [SECURITY.md](SECURITY.md).

Built by [AI Data House](https://braidscope.com).
