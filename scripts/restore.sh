#!/usr/bin/env bash
# Restore a backup made by scripts/backup.sh. REPLACES the current database and claim files.
#
#   scripts/restore.sh backups/claimguard-20261022-210000
#   scripts/restore.sh -f docker-compose.prod.yml /mnt/backups/claimguard-20261022-210000
set -euo pipefail

compose=(docker compose)
if [[ "${1:-}" == "-f" ]]; then
  compose+=(-f "$2")
  shift 2
fi
src="${1:?Usage: scripts/restore.sh [-f compose-file] <backup folder>}"
for f in database.dump storage.tar.gz SHA256SUMS; do
  [[ -f "$src/$f" ]] || { echo "Missing $src/$f" >&2; exit 1; }
done
(cd "$src" && shasum -a 256 -c SHA256SUMS) || { echo "Checksums do not match: backup damaged" >&2; exit 1; }

echo "This replaces ALL claims, findings, users and files with the backup in $src."
read -r -p "Type RESTORE to continue: " answer
[[ "$answer" == "RESTORE" ]] || { echo "Cancelled."; exit 1; }

echo "Stopping the API and worker"
"${compose[@]}" stop backend worker

echo "Restoring the database"
"${compose[@]}" exec -T postgres sh -c \
  'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner' < "$src/database.dump"

echo "Restoring claim files"
"${compose[@]}" run --rm -T --no-deps --entrypoint sh worker \
  -c 'find /data/storage -mindepth 1 -delete && tar -C /data/storage -xzf -' < "$src/storage.tar.gz"

echo "Starting the API and worker"
"${compose[@]}" start backend worker
echo "Restored from $src"
