#!/usr/bin/env bash
# Back up ClaimGuard: the database and the stored claim files (originals and page images).
#
#   scripts/backup.sh                                   # development stack (docker-compose.yml)
#   scripts/backup.sh -f docker-compose.prod.yml        # production stack
#   scripts/backup.sh -f docker-compose.prod.yml /mnt/backups
#
# Writes <dir>/claimguard-YYYYMMDD-HHMMSS/{database.dump,storage.tar.gz,SHA256SUMS}. Default dir: ./backups
# (ignored by git). Backups hold patient data: keep them encrypted and off this server, and test a restore.
set -euo pipefail

compose=(docker compose)
if [[ "${1:-}" == "-f" ]]; then
  compose+=(-f "$2")
  shift 2
fi
dir="${1:-backups}"
out="$dir/claimguard-$(date +%Y%m%d-%H%M%S)"
umask 077
mkdir -p "$out"

echo "Database -> $out/database.dump"
"${compose[@]}" exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' \
  > "$out/database.dump"

echo "Claim files -> $out/storage.tar.gz"
"${compose[@]}" exec -T worker tar -C /data/storage -czf - . > "$out/storage.tar.gz"

(cd "$out" && shasum -a 256 database.dump storage.tar.gz > SHA256SUMS)
du -sh "$out"/* | sed 's/^/  /'
echo "Done: $out"
