"""Speedtest: measures write speed to TrueNAS over current connection."""

import os
import time
import threading
from pathlib import Path
from typing import Optional

from app.config import load_config, _human_size
from app.transfer import _mount_smb, SMB_MOUNT_POINT

# Speedtest state
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
SPEEDTEST_FILENAME = ".pi-archiver-speedtest.tmp"


def get_speedtest_state() -> dict:
    with _speedtest_lock:
        return _speedtest_state.copy()


def start_speedtest(total_pending_bytes: int = 0) -> bool:
    """Start a speedtest in background. Returns False if already running."""
    with _speedtest_lock:
        if _speedtest_state["running"]:
            return False
        _speedtest_state.update({
            "running": True,
            "progress": 0,
            "speed": 0.0,
            "elapsed": 0.0,
            "error": "",
            "result": None,
        })

    thread = threading.Thread(
        target=_run_speedtest, args=(total_pending_bytes,), daemon=True
    )
    thread.start()
    return True


def _run_speedtest(total_pending_bytes: int):
    """Write test data to NAS for SPEEDTEST_DURATION seconds, measure throughput."""
    test_file = None
    try:
        # Mount SMB
        mount_point = _mount_smb()
        if not mount_point:
            with _speedtest_lock:
                _speedtest_state["running"] = False
                _speedtest_state["error"] = "Nem sikerült csatlakozni a NAS-hoz"
            return

        config = load_config()
        nas_base_path = config["truenas"].get("path", "/").strip("/")
        dest_dir = Path(mount_point)
        if nas_base_path:
            dest_dir = dest_dir / nas_base_path
        dest_dir.mkdir(parents=True, exist_ok=True)

        test_file = dest_dir / SPEEDTEST_FILENAME

        # Generate random-ish data chunk (not /dev/urandom for speed, just repeated bytes)
        chunk = os.urandom(SPEEDTEST_CHUNK_SIZE)

        total_written = 0
        start_time = time.monotonic()
        samples = []

        with open(test_file, "wb") as f:
            while True:
                elapsed = time.monotonic() - start_time
                if elapsed >= SPEEDTEST_DURATION:
                    break

                f.write(chunk)
                f.flush()
                os.fsync(f.fileno())
                total_written += SPEEDTEST_CHUNK_SIZE

                current_speed = total_written / elapsed if elapsed > 0 else 0
                samples.append(current_speed)

                with _speedtest_lock:
                    _speedtest_state["progress"] = min(
                        int((elapsed / SPEEDTEST_DURATION) * 100), 99
                    )
                    _speedtest_state["speed"] = current_speed
                    _speedtest_state["elapsed"] = elapsed

        end_time = time.monotonic()
        duration = end_time - start_time
        avg_speed = total_written / duration if duration > 0 else 0

        # Calculate ETA for pending data
        eta_str = ""
        if total_pending_bytes > 0 and avg_speed > 0:
            eta_seconds = total_pending_bytes / avg_speed
            eta_str = _format_eta(eta_seconds)

        result = {
            "avg_speed": avg_speed,
            "avg_speed_human": f"{_human_size(int(avg_speed))}/s",
            "total_written": total_written,
            "total_written_human": _human_size(total_written),
            "duration": round(duration, 1),
            "pending_bytes": total_pending_bytes,
            "pending_human": _human_size(total_pending_bytes),
            "estimated_eta": eta_str,
        }

        with _speedtest_lock:
            _speedtest_state["running"] = False
            _speedtest_state["progress"] = 100
            _speedtest_state["result"] = result

        # Discord notification
        from app.discord_notify import notify_speedtest_result
        notify_speedtest_result(avg_speed, eta_str, total_pending_bytes)

    except Exception as e:
        with _speedtest_lock:
            _speedtest_state["running"] = False
            _speedtest_state["error"] = str(e)
    finally:
        # Clean up test file
        try:
            if test_file and test_file.exists():
                test_file.unlink()
        except Exception:
            pass


def _format_eta(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}mp"
    elif seconds < 3600:
        m = int(seconds // 60)
        s = int(seconds % 60)
        return f"{m}p {s}mp"
    else:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        return f"{h}ó {m}p"
