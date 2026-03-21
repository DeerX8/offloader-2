# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Pi Archiver** is a Raspberry Pi-based file archival system. It transfers files from USB SSDs to a TrueNAS storage server via rsync. A mobile-optimized single-page web app (Flask backend, vanilla JS + Tailwind frontend) runs on the Pi and is accessed remotely via Tailscale VPN. The UI is in Hungarian.

## Development Commands

```bash
# Run locally (development)
FLASK_APP=app.main:create_app flask run --host=0.0.0.0 --port=5000

# Install system dependencies + set up service (Linux/Pi only)
sudo ./install.sh

# Update after git pull
cd pi-archiver && git pull origin main && sudo ./update.sh

# Service management (on Pi)
sudo systemctl status pi-archiver
sudo journalctl -u pi-archiver -f
sudo systemctl restart pi-archiver
```

No build step, no linter config, no test suite — pure Python + vanilla JS.

**Python dependencies:** `flask >= 3.0`, `flask-compress >= 1.15`, `requests >= 2.31`

## Architecture

### Backend (`app/`)

- **`main.py`** — Flask app factory (`create_app()`), enables gzip compression, serves the SPA
- **`routes.py`** — All API endpoints as a single `api` Blueprint (~20 endpoints under `/api/`)
- **`config.py`** — Loads/saves `/etc/pi-archiver/config.json`; archive fingerprint tracking via `/etc/pi-archiver/archive.json`
- **`storage.py`** — USB detection (`lsblk -J`), mounting (read-only first, fallback RW), file listing with archive status
- **`transfer.py`** — Core transfer engine: `TransferJob` dataclass, `TransferStatus` enum, three transfer modes (SSH/rsyncd/SMB), retry logic, rsync progress parsing, Discord notifications, threaded execution
- **`speedtest.py`** — Measures NAS write speed by rsyncing random data; saves result to config for ETA calculations
- **`discord_notify.py`** — Async Discord webhook embeds (start/progress/complete/error/speedtest)

### Frontend

Single file: `app/templates/index.html` (~2000 lines). Three tabs: **Files** (USB browser + filter + destination picker), **Transfers** (progress bars, speedtest), **Settings** (TrueNAS config, transfer mode, Discord). Uses `fetch` polling for real-time transfer progress.

### Transfer Flow

1. Frontend selects files → POST `/api/transfer` → backend creates `TransferJob`
2. Transfer runs in a daemon thread; rsync output is parsed by regex for progress/speed/ETA
3. Frontend polls `GET /api/transfer/<job_id>` for live updates
4. Discord webhooks fire asynchronously at start, progress intervals, and completion

### Three Transfer Modes

| Mode | Mechanism |
|------|-----------|
| SSH (default) | `rsync -e ssh` with Ed25519 keys |
| rsyncd | Direct rsync daemon (port 873) |
| SMB | Mount SMB share first, then local rsync |

### Runtime Data

- `/etc/pi-archiver/config.json` — All settings (TrueNAS host/share/path/credentials, transfer mode, bandwidth limit, retry config, Discord webhook)
- `/etc/pi-archiver/archive.json` — File fingerprints (`name|size|mtime`) to skip already-transferred files
- `/mnt/pi-archiver/*` — USB mount points
- `/opt/pi-archiver` — Deployed application files
