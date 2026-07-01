# Deploying CHRGD Content Studio (Phase A3)

Get the dashboard live at **https://contentstudio.getchrgd.co.uk** on a small
always-on VPS, with automatic HTTPS via Caddy. Total time ~30 minutes; running
cost ~£4/mo plus API usage.

Everything the app writes (SQLite DB + rendered assets) lives on the box's disk,
and one process runs both the web app and the background job worker.

---

## 0. What you need
- A VPS (recommended: **Hetzner CX22** — 2 vCPU / 4 GB, ~€4.50/mo — or a
  DigitalOcean 2 GB droplet, ~$12, or any Ubuntu 22.04/24.04 box).
- Your domain **on Cloudflare** (it is), so you can add one DNS record.
- Your **OpenAI API key**.

---

## 1. Create the VPS
Spin up an **Ubuntu 24.04** server. Note its **public IP** (e.g. `203.0.113.10`).
Make sure the firewall/security group allows inbound **22, 80, 443**.

SSH in:
```bash
ssh root@203.0.113.10
```

## 2. Point the subdomain at it (Cloudflare)
In the Cloudflare dashboard → your `getchrgd.co.uk` zone → **DNS → Records → Add
record**:

| Field | Value |
|-------|-------|
| Type  | `A` |
| Name  | `contentstudio` |
| IPv4  | `203.0.113.10`  ← your VPS IP |
| Proxy status | **DNS only (grey cloud)** — for the first certificate |

Save. That's the whole DNS job. (You can switch the cloud to **orange/proxied**
later — see §8.)

## 3. Bootstrap the server
```bash
git clone <your-repo-url> /opt/chrgd/app
cd /opt/chrgd/app
sudo bash deploy/setup.sh
```
This installs Python, ffmpeg, Caddy, creates the `chrgd` user, builds the
virtualenv, and installs the systemd unit + Caddyfile. (Prefer to do it by hand?
Every step it runs is listed in `deploy/setup.sh`.)

## 4. Configure secrets
```bash
sudo -u chrgd nano /opt/chrgd/app/.env
```
Set at least:
```ini
OPENAI_API_KEY=sk-...
CHRGD_IMAGE_MODEL=gpt-image-2
# Web login:
CHRGD_WEB_USERNAME=admin
CHRGD_WEB_PASSWORD_HASH=          # from the command below
CHRGD_SECRET_KEY=                 # from: openssl rand -hex 32
```
Generate the password hash and a session key:
```bash
/opt/chrgd/app/.venv/bin/python -m chrgd.webauth 'your-strong-password'
openssl rand -hex 32
```
Paste those into `.env`. Keep `CHRGD_VIDEO_ENABLED=false` for now.

## 5. Start it
```bash
sudo systemctl start chrgd
sudo systemctl reload caddy
```
Check health and logs:
```bash
systemctl status chrgd --no-pager
curl -s localhost:8000/healthz          # {"ok":true}
sudo journalctl -u chrgd -f             # app logs
```

## 6. Verify
Open **https://contentstudio.getchrgd.co.uk** — you should get a valid
certificate and the login page. Sign in with your username + password.

> First load may take ~30 s while Caddy fetches the certificate. If the cert
> doesn't issue, it's almost always the DNS record (make sure it's **grey
> cloud** for now) or a blocked port 80/443.

## 7. Day-to-day
**Update to a new version:**
```bash
cd /opt/chrgd/app && sudo -u chrgd git pull
sudo -u chrgd /opt/chrgd/app/.venv/bin/pip install -e ".[web,llm,media]"
sudo systemctl restart chrgd
```
**Backups (nightly):** add this cron line for the `chrgd` user
(`sudo crontab -u chrgd -e`):
```
0 3 * * *  /opt/chrgd/app/deploy/chrgd-backup.sh >> /opt/chrgd/backups/backup.log 2>&1
```
This keeps 14 days of DB snapshots + asset tarballs in `/opt/chrgd/backups`.

**Scheduled runs:** `chrgd run` chains scout→build→render→export. Trigger it
weekly with the bundled timer:
```bash
sudo cp deploy/chrgd-run.* /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now chrgd-run.timer
systemctl list-timers chrgd-run.timer     # confirm next run
```
Edit `deploy/chrgd-run.service` to change the flags (e.g. add `--scout`, adjust
`--count`) and `deploy/chrgd-run.timer` for the schedule. Runs honour
`CHRGD_MAX_SPEND_PER_RUN`, so a single run can't overspend.

## 8. Cloudflare proxy (optional hardening)
Once it works on grey cloud you can flip the record to **orange (proxied)** for
Cloudflare's CDN/DDoS protection and to hide the origin IP. If you do:
- Cloudflare → **SSL/TLS → Overview → Full (strict)**.
- Keep Caddy's Let's Encrypt cert (it's a valid origin cert), or install a
  Cloudflare **Origin Certificate** on the box.
- Cloudflare's default upload cap on some plans is 100 MB — fine for our assets.

## 9. Security checklist
- `.env` is `chmod 600`, owned by `chrgd`, never committed.
- App binds `127.0.0.1` only; Caddy is the sole public listener (80/443).
- Single strong web password (hashed) + a real `CHRGD_SECRET_KEY`.
- Keep the server patched (`unattended-upgrades`), restrict SSH (keys only).
- Consider Cloudflare Access in front for a second auth factor.

---

## Notes / gotchas
- **One process only.** The background worker is a singleton — the systemd unit
  runs a single uvicorn process on purpose. Don't add `--workers`.
- **Disk is the source of truth.** SQLite (`data/`) + assets (`output/`) live on
  the VPS disk; that's why backups matter. No external DB needed at this scale.
- **Video stays off** until Phase C. When enabled, `ffmpeg` (installed here) and
  a `HIGGSFIELD_API_KEY` are used; the job worker already handles async state.
