#!/usr/bin/env bash
# Nightly backup of the SQLite DB (hot, WAL-safe) + rendered assets.
# Install a cron entry (as the chrgd user):
#   0 3 * * *  /opt/chrgd/app/deploy/chrgd-backup.sh >> /opt/chrgd/backups/backup.log 2>&1
set -euo pipefail

APP="${CHRGD_APP_DIR:-/opt/chrgd/app}"
DEST="${CHRGD_BACKUP_DIR:-/opt/chrgd/backups}"
KEEP="${CHRGD_BACKUP_KEEP:-14}"

mkdir -p "$DEST"
STAMP="$(date +%Y%m%d_%H%M%S)"

# Consistent copy of a live (WAL) SQLite database.
sqlite3 "$APP/data/chrgd.db" ".backup '$DEST/chrgd_$STAMP.db'"

# Assets (best-effort; skip if empty).
if [ -d "$APP/output" ]; then
	tar czf "$DEST/output_$STAMP.tar.gz" -C "$APP" output
fi

# Retention: keep the newest $KEEP of each.
ls -1t "$DEST"/chrgd_*.db 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f
ls -1t "$DEST"/output_*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f

echo "$(date -Is) backup ok -> chrgd_$STAMP.db"
