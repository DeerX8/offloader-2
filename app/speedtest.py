"""Speedtest: measures actual write speed to TrueNAS using the configured transfer mode."""

import os
import subprocess
import time
import threading
from pathlib import Path

from app.config import load_config, _human_size
from app.transfer import mount_smb, SMB_MOUNT_POINT, _build_rsync_dest

_speedtest_state = {
    "running": False,
    "progress": 0,
    "speed": 0.0,
    "elapsed": 0.0,
    "error": "",
    "result": None,
}
_speedtest_lock = threading.Lock()

SPEEDTEST_DURATION = 12  # seconds
SPEEDTEST_CHUNK_SIZE = 4 * 1024 * 1024  # 4MB chunks
SPEEDTEST_MAX_SIZE = 64 * 1024 * 1024  # Max 64MB local file (safe for tmpfs/SD)
SPEEDTEST_FILENAME = ".pi-archiver-speedtest.tmp"
# Use SD card, NOT /tmp (which is often a small tmpfs in RAM on Pi)
SPEEDTEST_LOCAL_DIR = "/opt/pi-archiver"


def get_speedtest_state() -> dict:
    with _speedtest_lock:
        return _speedtest_state.copy()


def start_speedtest(total_pending_bytes: int = 0) -> bool:
    with _speedtest_lock:
        if _speedtest_state["running"]:
            return False
        _speedtest_state.update({
            "running": True, "progress": 0, "speed": 0.0,
            "elapsed": 0.0, "error": "", "result": None,
        })

    thread = threading.Thread(target=_run_speedtest, args=(total_pending_bytes,), daemon=True)
    thread.start()
    return True


def _run_speedtest(total_pending_bytes: int):
    """Generate a temp file locally, then rsync it to NAS using the configured mode.
    Measures actual transfer throughput including protocol overhead."""

    local_tmp = Path(SPEEDTEST_LOCAL_DIR) / SPEEDTEST_FILENAME
    config = load_config()
    mode = config["transfer"].get("mode", "ssh")

    try:
        # Ensure local dir exists
        Path(SPEEDTEST_LOCAL_DIR).mkdir(parents=True, exist_ok=True)

        # For SMB mode, mount first
        if mode == "smb":
            if not mount_smb():
                _fail("Nem sikerült csatlakozni a NAS-hoz (SMB mount)")
                return

        # Build destination using same logic as transfers
        extra_args, rsync_dest, rsync_env = _build_rsync_dest(config, "")

        # Generate test data locally (capped at SPEEDTEST_MAX_SIZE)
        chunk = os.urandom(SPEEDTEST_CHUNK_SIZE)
        total_written = 0
        start_time = time.monotonic()

        with open(local_tmp, "wb") as f:
            while True:
                elapsed = time.monotonic() - start_time
                if elapsed >= SPEEDTEST_DURATION:
                    break
                if total_written >= SPEEDTEST_MAX_SIZE:
                    break

                f.write(chunk)
                f.flush()
                total_written += SPEEDTEST_CHUNK_SIZE

                with _speedtest_lock:
                    _speedtest_state["progress"] = min(int((elapsed / SPEEDTEST_DURATION) * 100), 99)
                    _speedtest_state["elapsed"] = elapsed

        local_size = total_written

        # Now rsync the whole file and time it
        with _speedtest_lock:
            _speedtest_state["progress"] = 50

        rsync_start = time.monotonic()

        cmd = [
            "rsync", "-ah", "--no-inc-recursive",
            "--timeout=30", "--contimeout=15",
            "--inplace", "--no-perms", "--no-owner", "--no-group",
        ]
        cmd.extend(extra_args)
        cmd.extend([str(local_tmp), rsync_dest])

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=rsync_env)

        rsync_end = time.monotonic()
        rsync_duration = rsync_end - rsync_start

        if result.returncode != 0:
            _fail(f"rsync hiba: {result.stderr.strip()[:100]}")
            return

        avg_speed = local_size / rsync_duration if rsync_duration > 0 else 0

        # ETA for pending data
        eta_str = ""
        if total_pending_bytes > 0 and avg_speed > 0:
            eta_seconds = total_pending_bytes / avg_speed
            eta_str = _format_eta(eta_seconds)

        speedtest_result = {
            "avg_speed": avg_speed,
            "avg_speed_human": f"{_human_size(int(avg_speed))}/s",
            "total_written": local_size,
            "total_written_human": _human_size(local_size),
            "duration": round(rsync_duration, 1),
            "mode": mode,
            "pending_bytes": total_pending_bytes,
            "pending_human": _human_size(total_pending_bytes),
            "estimated_eta": eta_str,
        }

        with _speedtest_lock:
            _speedtest_state["running"] = False
            _speedtest_state["progress"] = 100
            _speedtest_state["speed"] = avg_speed
            _speedtest_state["result"] = speedtest_result

        # Discord
        from app.discord_notify import notify_speedtest_result
        notify_speedtest_result(avg_speed, eta_str, total_pending_bytes)

        # Clean up remote file
        _cleanup_remote(config, extra_args, rsync_dest, rsync_env)

    except Exception as e:
        _fail(str(e))
    finally:
        try:
            if local_tmp.exists():
                local_tmp.unlink()
        except Exception:
            pass


def _cleanup_remote(config, extra_args, rsync_dest, rsync_env):
    """Try to delete the speedtest file from remote."""
    mode = config["transfer"].get("mode", "ssh")
    host = config["truenas"]["host"]
    t = config["transfer"]

    try:
        if mode == "ssh":
            user = t.get("ssh_user", "root")
            key = t.get("ssh_key_path", "")
            port = t.get("ssh_port", 22)
            remote_base = t.get("ssh_remote_path", "").rstrip("/")
            remote_file = f"{remote_base}/{SPEEDTEST_FILENAME}"
            subprocess.run(
                ["ssh", "-i", key, "-p", str(port), "-o", "BatchMode=yes",
                 f"{user}@{host}", f"rm -f {remote_file}"],
                capture_output=True, timeout=10,
            )
        elif mode == "smb":
            test_file = Path(SMB_MOUNT_POINT)
            nas_path = config["truenas"].get("path", "/").strip("/")
            if nas_path:
                test_file = test_file / nas_path
            test_file = test_file / SPEEDTEST_FILENAME
            if test_file.exists():
                test_file.unlink()
        # rsyncd: no easy way to delete, file is small, will be overwritten next time
    except Exception:
        pass


def _fail(error: str):
    with _speedtest_lock:
        _speedtest_state["running"] = False
        _speedtest_state["error"] = error


def _format_eta(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}mp"
    elif seconds < 3600:
        return f"{int(seconds // 60)}p {int(seconds % 60)}mp"
    else:
        return f"{int(seconds // 3600)}ó {int((seconds % 3600) // 60)}p"
