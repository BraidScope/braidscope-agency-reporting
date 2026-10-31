# Self-host Braidscope

Run the whole thing on one Linux machine with Docker. Nothing leaves your server.
Tested target: 2 vCPU, 4 GB RAM minimum (8 GB comfortable), 20 GB disk, Ubuntu 22.04 or newer.

## Install (one command)

```bash
curl -fsSL https://raw.githubusercontent.com/BraidScope/braidscope-agency-reporting/main/deploy/selfhost/install.sh | bash
```

With a real domain (HTTPS turns on by itself; point the domain's A record at the server first):

```bash
BRAIDSCOPE_DOMAIN=app.example.com BRAIDSCOPE_EMAIL=you@example.com \
  bash -c "$(curl -fsSL https://raw.githubusercontent.com/BraidScope/braidscope-agency-reporting/main/deploy/selfhost/install.sh)"
```

The script checks Docker, downloads the code into `./braidscope`, writes `deploy/selfhost/.env` with
random secrets, starts everything, creates the first owner account and prints the login.

If you would rather read before you run, download the script first and open it. It is 120 lines.

## What runs

| Container | Job |
|---|---|
| `db` | Postgres 17 with the Supabase extensions |
| `auth` | Sign in, sign up, password reset, two-factor (GoTrue) |
| `rest` | Database over HTTP with row level security (PostgREST) |
| `storage` | Report PDFs, on a local volume |
| `migrate` | Applies the schema once, then exits |
| `redis` | Dashboard cache |
| `py` | Braidscope backend |
| `web` | Braidscope app |
| `caddy` | HTTPS and routing |

Everything is in `deploy/selfhost/docker-compose.yml`. Image versions are pinned.

## Day-to-day

```bash
cd braidscope/deploy/selfhost
docker compose ps            # health of every container
docker compose logs -f py    # backend logs
bash backup.sh               # database dump + PDF files into ./backups (keeps 14)
bash upgrade.sh              # backup, fetch latest, rebuild, migrate
```

## Moving from Supabase cloud to self-host

Already on braidscope.cloud or your own Supabase project? One script copies users (passwords and
two-factor included), workspaces, data and report PDFs:

```bash
cd braidscope/deploy/selfhost
SUPABASE_DB_URL='postgres://...' SUPABASE_SERVICE_KEY='...' SUPABASE_URL='https://xxxx.supabase.co' \
  bash import-from-supabase.sh
```

The three values come from the Supabase dashboard, Project Settings. They are used once and not saved.
Everyone signs in again with the same email and password.

## Settings

All settings live in `deploy/selfhost/.env`. The important ones:

| Setting | Meaning |
|---|---|
| `DOMAIN`, `PUBLIC_URL` | Where people open the app. `localhost` means plain http on port 80 and a red banner. |
| `DISABLE_SIGNUP` | `true` = nobody can create an account except through an invitation. |
| `SMTP_*` | Needed for password reset and invitation emails. Without SMTP, create users yourself and share the password. |
| `RESEND_API_KEY` | Optional. App notification emails. In-app notifications work without it. |
| `META_ADS_*`, `GA4_*`, `SHOPIFY_*` | Connector app credentials from each platform's developer console. |

Change a setting, then `docker compose up -d` to apply.

## Backups and restore

`backup.sh` writes two files into `backups/`: a database dump and a tarball of the PDF files.
Put it in cron:

```
0 3 * * * cd /opt/braidscope/deploy/selfhost && bash backup.sh
```

Restore:

```bash
gunzip -c backups/db-<date>.sql.gz | docker compose exec -T db psql -U postgres
docker run --rm -v braidscope_storagedata:/data -v $PWD/backups:/b alpine sh -c "cd /data && tar xzf /b/storage-<date>.tar.gz"
docker compose restart py storage
```

## HTTPS

Caddy gets a certificate from Let's Encrypt when `DOMAIN` is a real domain that resolves to the
server and ports 80 and 443 are open. No setup. Behind another proxy, forward both ports to Caddy or
terminate TLS there and set `PUBLIC_URL` to the https address.

## Security notes

- Secrets are random per install and live only in `.env` (mode 600). Rotate by editing `.env` and restarting. Changing `JWT_SECRET` signs everyone out.
- The database, auth, rest and storage ports are not published. Only Caddy listens on 80 and 443.
- Keep the server updated and behind a firewall that allows 22, 80 and 443 only.
- Report a security problem through GitHub's private vulnerability reporting on the repo, not a public issue.

## Uninstall

```bash
cd braidscope/deploy/selfhost
docker compose down -v      # -v deletes the data volumes too
```
