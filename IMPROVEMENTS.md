# Pi Archiver — Improvement Backlog

Based on a detailed interview about real-world usage patterns, pain points, and desired features.

---

## Deployment Context

- **User:** Solo photographer/videographer
- **Pi connectivity:** 5G modem via RJ45 (planning built-in 5G HAT)
- **Access:** 80% remote over Tailscale VPN, 20% local LAN
- **Clients:** Phone + laptop depending on situation
- **Typical session:** < 50 GB RAW photos + video, 30 min–1 hour transfer time
- **Destination:** TrueNAS, folder per shoot (varies)

---

## Priority 1 — Failure Recovery (Cell Tower Drops)

**Problem:** The Pi is on 5G. During cell tower handoffs the connection drops for a few seconds. rsync hangs or dies. The retry logic exists but has never been tested under real failure. Current recovery is entirely manual: re-select files, visually skip files that have the archive badge, restart.

### 1.1 Add `--timeout` to rsync invocations (`transfer.py`)

**Impact: High. One-line fix.**

Without `--timeout`, rsync hangs on a dead TCP connection indefinitely. Adding it makes rsync detect the drop quickly, fail fast, and let the retry loop take over.

```python
# In _rsync_file() / _rsync_with_retry(), add to the cmd list:
'--timeout=30'
```

Combined with `--partial` (already present) and retry delay (~5–10s to allow cellular reconnect), the flow becomes:
1. Tower drop → rsync detects dead socket within 30s
2. rsync exits with error
3. Retry logic waits (delay should be ≥ 5s to let 5G reconnect)
4. rsync re-runs, resumes partial file

### 1.2 Increase default retry delay (`config.py`)

Current default retry delay is **3 seconds** — too short for a 5G reconnect. Change default to **10 seconds**.

```python
# In DEFAULT_CONFIG:
'retry_delay': 10  # was 3
```

### 1.3 "Retry failed job" button (UI + backend)

**Impact: High UX improvement.**

After a failure, user currently re-selects all files manually. A retry button should re-queue only files that did not complete in the failed job.

- Backend: `TransferJob` should track per-file completion status (not just whole-job)
- API: `POST /api/transfer/<job_id>/retry` re-queues incomplete files
- UI: Show "Retry" button on failed jobs in the Transfers tab

### 1.4 Per-file completion tracking within a job

Currently the archive fingerprint is written only when a file fully transfers. For partial jobs, it's unclear which files made it. The job should track a list of completed file paths so retry can skip them without relying on the archive.

---

## Priority 2 — UI / Progress Visibility

### 2.1 Resume polling on tab reopen (Page Visibility API)

**Problem:** If the phone screen locks or the user switches apps, polling stops. User wants the UI to catch up when reopened.

```javascript
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) {
    // restart polling immediately
    resumePolling();
  }
});
```

### 2.2 Progress state should survive page reload

If the user closes and reopens the browser, active transfer jobs should be visible again. The backend already holds job state — the frontend just needs to fetch active jobs on load and resume polling them.

---

## Priority 3 — Checksum Verification

**Problem:** Archive badge (name+size+mtime fingerprint) is the only integrity signal. User wants optional post-transfer verification.

### 3.1 "Verify transfer" button per completed job

- Trigger: Manual button on completed job in Transfers tab
- Mechanism: Re-run rsync with `--checksum` and `--dry-run` — rsync will report any differences without re-transferring
- Display: Per-file pass/fail, or a simple "all files verified" / "X files differ" summary
- No automatic verification — keep it optional to avoid slowing normal workflow

---

## Priority 4 — Parallel Transfers (Nice to Have)

User runs one job at a time but expressed interest in parallel jobs for certain workflows.

- Today: all jobs run sequentially in a single daemon thread model
- Desired: ability to run 2+ jobs concurrently (e.g., different source drives to different destinations)
- Constraint: bandwidth limiting should apply across all concurrent jobs (not per-job)

---

## Known Risks Accepted (Do Not Over-Engineer)

| Risk | Decision |
|------|----------|
| Archive fingerprint collision (same name+size+mtime, different content) | Accepted — unlikely in practice |
| Flask running as root | Accepted — private device behind VPN |
| No automated tests | Accepted — intentionally lightweight project |
| Monolithic `index.html` (~2000 lines) | Accepted — no build step is a feature |
| No external health monitoring | Accepted — user finds out by trying to connect |

---

## What Is Working Well (Don't Touch)

- Mobile UI feels good in the field on phone
- Discord notification on completion is sufficient (nice to have, not critical)
- `--partial` flag already enables file resume — just needs `--timeout` to trigger it properly
- Tailscale + SSH key setup was smooth
- Three transfer modes (SSH/rsyncd/SMB) cover real use cases
- Speedtest for ETA estimation is useful

---

## Quick Wins Summary

| Change | File | Effort |
|--------|------|--------|
| Add `--timeout=30` to rsync cmd | `app/transfer.py` | Trivial |
| Increase default retry delay to 10s | `app/config.py` | Trivial |
| Page Visibility API for polling resume | `app/templates/index.html` | Small |
| Resume active jobs on page load | `app/templates/index.html` | Small |
| Per-file completion tracking in job | `app/transfer.py` | Medium |
| "Retry failed job" button | `app/transfer.py` + `routes.py` + `index.html` | Medium |
| "Verify transfer" button (rsync --checksum) | `app/transfer.py` + `routes.py` + `index.html` | Medium |
