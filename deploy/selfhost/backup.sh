#!/usr/bin/env bash
# Nightly-safe backup: database dump plus the report PDF files.
# Keeps the last 14. Run from this folder, by hand or from cron:
#   0 3 * * * cd /opt/braidscope/deploy/selfhost && bash backup.sh
# Restore:
#   gunzip -c backups/db-<date>.sql.gz | docker compose exec -T db psql -U postgres
#   docker run --rm -v braidscope_storagedata:/data -v $PWD/backups:/b alpine sh -c "cd /data && tar xzf /b/storage-<date>.tar.gz"
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p backups
stamp="$(date +%Y%m%d-%H%M%S)"
docker compose exec -T db pg_dump -U postgres --clean --if-exists postgres | gzip > "backups/db-$stamp.sql.gz"
docker run --rm -v braidscope_storagedata:/data:ro -v "$PWD/backups:/b" alpine tar czf "/b/storage-$stamp.tar.gz" -C /data .
ls -1t backups/db-*.sql.gz | tail -n +15 | xargs -r rm -f
ls -1t backups/storage-*.tar.gz | tail -n +15 | xargs -r rm -f
echo "backup written: backups/db-$stamp.sql.gz and backups/storage-$stamp.tar.gz"
