# CHRGD Content Studio — Windows Quickstart

A no-jargon guide to (A) run it on your Windows laptop and do a real test in
~10 minutes, then (B) put it live at `contentstudio.getchrgd.co.uk` when you're
ready. You can copy-paste every command exactly.

> Do **Part A first**. It confirms everything works (and your OpenAI key) on
> your laptop before you touch a server. Part B is the same app, just hosted.

---

## Part A — Run it on your laptop (real test)

### 1. Install two free things
- **Python** — https://www.python.org/downloads/ → big yellow "Download".
  Run the installer and **tick "Add python.exe to PATH"** on the first screen
  before clicking Install. (This checkbox matters.)
- **Git** — https://git-scm.com/download/win → install with all the defaults.

### 2. Get the code
Open **PowerShell** (press Start, type `PowerShell`, hit Enter) and paste these
one line at a time:

```powershell
cd $HOME\Desktop
git clone https://github.com/lsai1234/getchrgd-content-studio.git
cd getchrgd-content-studio
git checkout claude/milestone-1-build-7dvdla
```

That downloads the project to a `getchrgd-content-studio` folder on your Desktop.

### 3. Install the app
```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[web,llm,media]"
```
(The last line takes a minute or two — it's pulling in the pieces the app needs.)

### 4. Set your key + password
First make your settings file and open it in Notepad:
```powershell
copy .env.example .env
notepad .env
```
In Notepad, find these lines and fill them in (leave everything else as-is):
```ini
OPENAI_API_KEY=sk-...          # paste your key from platform.openai.com
CHRGD_WEB_PASSWORD=choose-a-password
CHRGD_SECRET_KEY=paste-the-random-string-from-the-next-step
```
Get a random value for `CHRGD_SECRET_KEY` by running this and copying the output:
```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"
```
Save the Notepad file and close it.

> You need a few dollars of credit on your OpenAI account
> (platform.openai.com → Billing). A real build + 5 images costs cents.

### 5. Start it
```powershell
.\.venv\Scripts\chrgd.exe serve
```
Leave that window open (it's the server). Open your browser to:

**http://127.0.0.1:8000**

Log in with username `admin` and the password you chose.

### 6. Do a real test (in the browser)
1. **Backlog** → type an idea in the capture box (e.g. *"gym bros who hog the
   squat rack for 40 minutes"*) → **Capture**.
2. **Build** → **Build next** (leave dry-run unticked for a real run). Watch the
   Jobs panel — it'll go PROCESSING → COMPLETED. This calls OpenAI for real.
3. **Review** → your post appears. Click **Render** (untick dry-run for real
   images) → wait, then refresh → you'll see 5 finished slides. Tweak any copy,
   then **Approve**.
4. **Export** → **Export week** → download the CSV. That's the file you'd
   bulk-upload to Metricool.

To stop the server later, click the PowerShell window and press `Ctrl + C`.
To start it again another day: open PowerShell, `cd $HOME\Desktop\getchrgd-content-studio`,
then `.\.venv\Scripts\chrgd.exe serve`.

**If anything errors, copy the red text and send it to me — that's exactly what
I need to help.**

---

## Part B — Put it live at contentstudio.getchrgd.co.uk

Once Part A works, hosting it means renting a small always-on server (~£4/mo)
so the site is reachable from your phone at your domain. Full instructions are
in **[DEPLOY.md](DEPLOY.md)**; the short version from your Windows laptop:

1. **Create a server** (Hetzner or DigitalOcean, Ubuntu 24.04). Note its IP.
2. **Connect to it** from PowerShell (Windows has SSH built in):
   ```powershell
   ssh root@YOUR_SERVER_IP
   ```
3. On the server, run the bootstrap (this is all in DEPLOY.md, steps 3–5):
   clone the repo, run `sudo bash deploy/setup.sh`, edit `.env`, start it.
4. **Point your domain:** in Cloudflare, add an `A` record named
   `contentstudio` to the server's IP (DEPLOY.md §2).
5. Visit **https://contentstudio.getchrgd.co.uk** and log in.

I'll walk you through Part B live when you get there — just say "ready to
deploy" and have the server IP handy.

---

## Quick reference

| I want to… | Do this |
|---|---|
| Start the app | `.\.venv\Scripts\chrgd.exe serve` then open http://127.0.0.1:8000 |
| Stop the app | Click the PowerShell window, press `Ctrl + C` |
| Get latest updates | `git pull` then re-run the `pip install` line from step 3 |
| Change my password | Edit `CHRGD_WEB_PASSWORD` in `.env`, restart the app |
| Do everything from the command line instead | `chrgd capture`, `chrgd build`, `chrgd render`, `chrgd export` (see README.md) |
