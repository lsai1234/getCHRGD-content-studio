#!/usr/bin/env bash
# One-time server bootstrap for CHRGD Content Studio on Ubuntu 22.04/24.04.
# Run as root on a fresh VPS:
#   git clone <repo> /opt/chrgd/app && cd /opt/chrgd/app
#   sudo bash deploy/setup.sh
#
# Idempotent-ish: safe to re-run. Review before use — it installs packages,
# creates a system user, and enables services. See DEPLOY.md for the manual
# walk-through if you'd rather do it by hand.
set -euo pipefail

APP=/opt/chrgd/app
USER=chrgd

if [ "$(id -u)" -ne 0 ]; then
	echo "Run as root (sudo bash deploy/setup.sh)." >&2
	exit 1
fi

echo "==> Installing system packages"
apt-get update -y
apt-get install -y python3 python3-venv python3-pip ffmpeg sqlite3 git \
	debian-keyring debian-archive-keyring apt-transport-https curl

echo "==> Installing Caddy (official repo)"
if ! command -v caddy >/dev/null 2>&1; then
	curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
		| gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
	curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
		> /etc/apt/sources.list.d/caddy-stable.list
	apt-get update -y && apt-get install -y caddy
fi

echo "==> Creating service user '$USER'"
id -u "$USER" >/dev/null 2>&1 || useradd --system --home "$APP" --shell /usr/sbin/nologin "$USER"

echo "==> Python virtualenv + dependencies"
cd "$APP"
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -e ".[web,llm,media]"

echo "==> Directories + ownership"
mkdir -p "$APP/data" "$APP/output" /opt/chrgd/backups /var/log/caddy
chown -R "$USER:$USER" /opt/chrgd

if [ ! -f "$APP/.env" ]; then
	cp "$APP/.env.example" "$APP/.env"
	chmod 600 "$APP/.env"
	chown "$USER:$USER" "$APP/.env"
	echo "==> Wrote $APP/.env from the example — EDIT IT before starting (keys, web password, secret)."
fi

echo "==> Installing systemd unit + Caddyfile"
cp "$APP/deploy/chrgd.service" /etc/systemd/system/chrgd.service
cp "$APP/deploy/Caddyfile" /etc/caddy/Caddyfile
systemctl daemon-reload
systemctl enable chrgd

cat <<'NEXT'

==> Bootstrap complete. Remaining steps:
  1. Edit /opt/chrgd/app/.env  (OPENAI_API_KEY, CHRGD_WEB_PASSWORD_HASH,
     CHRGD_SECRET_KEY, ...). Generate the password hash with:
        /opt/chrgd/app/.venv/bin/python -m chrgd.webauth 'your-password'
     Generate a session key with:
        openssl rand -hex 32
  2. In Cloudflare, add an A record: contentstudio -> <this server's IP>
     (grey cloud / DNS-only for the first cert; you can proxy later).
  3. Start it:
        sudo systemctl start chrgd
        sudo systemctl reload caddy
  4. Visit https://contentstudio.getchrgd.co.uk and log in.
NEXT
