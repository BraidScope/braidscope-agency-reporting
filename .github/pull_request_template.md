## Ticket
BS-0xx

## What changed
- 

## Service area
- [ ] `web/`
- [ ] `py/`
- [ ] connector (`py/app/connectors/` or a `py/app/<platform>*.py` module)
- [ ] infra/docs only
- [ ] contract change across services

## Checklist
- [ ] I branched from `main` (or `staging` for staging-only work).
- [ ] Every commit is signed off (`git commit -s`).
- [ ] I touched only one service folder, unless this is marked as a contract change.
- [ ] I ran the local checks listed in `AGENTS.md` (ruff, unit tests, web build).
- [ ] Docker compose still builds, or I explained why it was not run.
- [ ] No `.env` files, API keys, tokens, or passwords are committed.

## Security (every PR)
- [ ] No platform token or secret can reach a log line, an error message, a response body or the browser.
- [ ] Any new table the browser can reach has RLS policies; backend-only tables have RLS on with no browser policies.
- [ ] Connector change: every new non-GET call is listed in `py/app/connectors/policy.py` with its purpose, scopes are read-only (or `scope_note` says why not), tests use fixtures. See `docs/connectors.md`.
- [ ] Routing still matches `AGENTS.md` if I touched Caddy or service ports.

## Notes for reviewer
- 
