"""File transfer management: SMB mount + rsync with retry, archive tracking, Discord."""

import os
import re
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

from app.config import load_config, mark_archived, _human_size


class TransferStatus(str, Enum):
    PENDING = "pending"
    MOUNTING = "mounting"
    TRANSFERRING = "transferring"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TransferJob:
    id: str
    files: list[str]
    file_sizes: list[int]
    destination: str
    status: TransferStatus = TransferStatus.PENDING
    progress: float = 0.0
    speed: str = ""
    avg_speed: float = 0.0
    eta: str = ""
    current_file: str = ""
    files_done: int = 0
    files_total: int = 0
    total_bytes: int = 0
    bytes_transferred: int = 0
    error: str = ""
    started_at: float = 0
    finished_at: float = 0
    retry_count: int = 0
    # Discord notification tracking
    _last_notified_pct: int = -1

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "status": self.status.value,
            "progress": self.progress,
            "speed": self.speed,
            "avg_speed": f"{_human_size(int(self.avg_speed))}/s" if self.avg_speed > 0 else "",
            "eta": self.eta,
            "current_file": self.current_file,
            "files_done": self.files_done,
            "files_total": self.files_total,
            "total_bytes": self.total_bytes,
            "total_bytes_human": _human_size(self.total_bytes),
            "bytes_transferred": self.bytes_transferred,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "retry_count": self.retry_count,
        }


_transfers: dict[str, TransferJob] = {}
_transfer_lock = threading.Lock()

SMB_MOUNT_POINT = "/mnt/pi-archiver-nas"


def get_transfers() -> list[dict]:
    with _transfer_lock:
        return [t.to_dict() for t in _transfers.values()]


def get_transfer(job_id: str) -> Optional[dict]:
    with _transfer_lock:
        job = _transfers.get(job_id)
        return job.to_dict() if job else None


def cancel_transfer(job_id: str) -> bool:
    with _transfer_lock:
        job = _transfers.get(job_id)
        if job and job.status in (TransferStatus.PENDING, TransferStatus.TRANSFERRING, TransferStatus.MOUNTING):
            job.status = TransferStatus.CANCELLED
            return True
    return False


def start_transfer(source_files: list[str], file_sizes: list[int], destination: str) -> str:
    """Start a new transfer job.

    Args:
        source_files: Absolute paths to files on USB device
        file_sizes: Size in bytes for each file (same order)
        destination: Relative path within TrueNAS share
    """
    job_id = str(uuid.uuid4())[:8]
    total = sum(file_sizes)

    job = TransferJob(
        id=job_id,
        files=source_files,
        file_sizes=file_sizes,
        destination=destination,
        files_total=len(source_files),
        total_bytes=total,
    )

    with _transfer_lock:
        _transfers[job_id] = job

    thread = threading.Thread(target=_run_transfer, args=(job,), daemon=True)
    thread.start()
    return job_id


def check_smb_connection() -> dict:
    config = load_config()
    nas = config["truenas"]

    if not nas["host"] or not nas["share"]:
        return {"connected": False, "error": "TrueNAS nincs konfigurálva"}

    try:
        cmd = ["smbclient", "-L", f"//{nas['host']}", "-N", "--timeout=5"]
        if nas.get("username"):
            cmd = [
                "smbclient", "-L", f"//{nas['host']}",
                "-U", f"{nas['username']}%{nas['password']}",
                "--timeout=5",
            ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            return {"connected": True, "host": nas["host"], "share": nas["share"]}
        return {"connected": False, "error": f"Kapcsolódási hiba: {result.stderr.strip()[:100]}"}
    except subprocess.TimeoutExpired:
        return {"connected": False, "error": "Időtúllépés - szerver nem elérhető"}
    except Exception as e:
        return {"connected": False, "error": str(e)}


def _mount_smb() -> Optional[str]:
    config = load_config()
    nas = config["truenas"]

    mount_point = Path(SMB_MOUNT_POINT)
    mount_point.mkdir(parents=True, exist_ok=True)

    if _is_mounted(str(mount_point)):
        return str(mount_point)

    mount_opts = [
        f"username={nas.get('username', 'guest')}",
        f"password={nas.get('password', '')}",
        "iocharset=utf8",
        "file_mode=0664",
        "dir_mode=0775",
        "vers=3.0",
        "nofail",
    ]

    try:
        result = subprocess.run(
            [
                "mount", "-t", "cifs",
                f"//{nas['host']}/{nas['share']}",
                str(mount_point),
                "-o", ",".join(mount_opts),
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return str(mount_point)
        print(f"SMB mount failed: {result.stderr}")
        return None
    except Exception as e:
        print(f"SMB mount exception: {e}")
        return None


def _is_mounted(path: str) -> bool:
    try:
        result = subprocess.run(
            ["mountpoint", "-q", path],
            capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def _run_transfer(job: TransferJob):
    """Execute the transfer with retry logic and notifications."""
    from app.discord_notify import (
        notify_transfer_start,
        notify_transfer_complete,
        notify_transfer_error,
    )

    try:
        job.started_at = time.time()

        # Mount SMB
        job.status = TransferStatus.MOUNTING
        mount_point = _mount_smb()
        if not mount_point:
            job.status = TransferStatus.FAILED
            job.error = "Nem sikerült csatlakozni a TrueNAS-hoz (SMB mount hiba)"
            notify_transfer_error(job.id, job.error, 0, job.files_total)
            return

        if job.status == TransferStatus.CANCELLED:
            return

        # Prepare destination
        config = load_config()
        nas_base_path = config["truenas"].get("path", "/").strip("/")
        dest_subpath = job.destination.strip("/")

        dest_dir = Path(mount_point)
        if nas_base_path:
            dest_dir = dest_dir / nas_base_path
        if dest_subpath:
            dest_dir = dest_dir / dest_subpath
        dest_dir.mkdir(parents=True, exist_ok=True)

        # Discord: transfer start
        notify_transfer_start(
            job.id, job.files_total, job.total_bytes, job.destination
        )

        # Transfer files
        job.status = TransferStatus.TRANSFERRING
        bytes_before = 0

        for i, (source_file, file_size) in enumerate(zip(job.files, job.file_sizes)):
            if job.status == TransferStatus.CANCELLED:
                return

            source_path = Path(source_file)
            if not source_path.exists():
                job.error = f"Fájl nem található: {source_path.name}"
                continue

            job.current_file = source_path.name
            job.files_done = i

            # rsync with retry
            success = _rsync_with_retry(job, str(source_path), str(dest_dir))

            if success:
                # Mark as archived
                stat = source_path.stat()
                mark_archived(
                    source_path.name, stat.st_size, stat.st_mtime, job.destination
                )
                bytes_before += file_size
                job.bytes_transferred = bytes_before
            elif job.status != TransferStatus.CANCELLED:
                job.status = TransferStatus.FAILED
                if not job.error:
                    job.error = f"Hiba a másolás közben: {source_path.name}"
                notify_transfer_error(job.id, job.error, job.files_done, job.files_total)
                return

        if job.status == TransferStatus.CANCELLED:
            return

        job.files_done = job.files_total
        job.bytes_transferred = job.total_bytes
        job.progress = 100.0
        job.status = TransferStatus.COMPLETED
        job.finished_at = time.time()

        duration = job.finished_at - job.started_at
        avg_speed = job.total_bytes / duration if duration > 0 else 0
        job.avg_speed = avg_speed

        notify_transfer_complete(job.id, job.files_total, job.total_bytes, duration, avg_speed)

    except Exception as e:
        job.status = TransferStatus.FAILED
        job.error = f"Váratlan hiba: {str(e)}"
        from app.discord_notify import notify_transfer_error
        notify_transfer_error(job.id, job.error, job.files_done, job.files_total)


def _rsync_with_retry(job: TransferJob, source: str, dest_dir: str) -> bool:
    """Rsync a single file with automatic retry on failure."""
    config = load_config()
    max_retries = config["transfer"].get("retry_attempts", 5)
    retry_delay = config["transfer"].get("retry_delay", 3)

    for attempt in range(max_retries + 1):
        if job.status == TransferStatus.CANCELLED:
            return False

        success = _rsync_file(job, source, dest_dir)
        if success:
            return True

        if attempt < max_retries:
            job.retry_count += 1
            job.error = f"Újrapróbálkozás ({attempt + 1}/{max_retries})..."
            print(f"Retry {attempt + 1}/{max_retries} for {source}, waiting {retry_delay}s")

            # Wait, but check for cancellation
            for _ in range(retry_delay):
                if job.status == TransferStatus.CANCELLED:
                    return False
                time.sleep(1)

            # Re-check SMB mount (might have disconnected)
            if not _is_mounted(SMB_MOUNT_POINT):
                print("SMB disconnected, remounting...")
                mount_point = _mount_smb()
                if not mount_point:
                    job.error = "NAS kapcsolat megszakadt, újracsatlakozás sikertelen"
                    return False

    return False


def _rsync_file(job: TransferJob, source: str, dest_dir: str) -> bool:
    """Rsync a single file with progress tracking and Discord notifications."""
    from app.discord_notify import notify_transfer_progress

    config = load_config()
    transfer_cfg = config["transfer"]
    discord_cfg = config.get("discord", {})
    notify_interval = discord_cfg.get("notify_interval", 10)

    cmd = [
        "rsync",
        "-ah",
        "--info=progress2",
        "--no-inc-recursive",
        "--timeout=30",
        "--contimeout=15",
    ]

    if transfer_cfg.get("partial_transfer", True):
        cmd.append("--partial")

    bw_limit = transfer_cfg.get("bandwidth_limit", 0)
    if bw_limit > 0:
        cmd.extend(["--bwlimit", str(bw_limit)])

    cmd.extend([source, dest_dir + "/"])

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        progress_re = re.compile(
            r"[\d,]+\s+(\d+)%\s+([\d.]+\w+/s)\s+([\d:]+)"
        )

        for line in iter(proc.stdout.readline, ""):
            if job.status == TransferStatus.CANCELLED:
                proc.terminate()
                return False

            match = progress_re.search(line)
            if match:
                file_progress = int(match.group(1))
                base_progress = (job.files_done / job.files_total) * 100
                file_contribution = (1 / job.files_total) * file_progress
                overall_pct = min(base_progress + file_contribution, 99.9)
                job.progress = overall_pct
                job.speed = match.group(2)
                job.eta = match.group(3)

                # Calculate average speed
                elapsed = time.time() - job.started_at
                if elapsed > 0 and job.bytes_transferred > 0:
                    job.avg_speed = job.bytes_transferred / elapsed

                # Discord progress notification at configured intervals
                pct_bucket = int(overall_pct // notify_interval) * notify_interval
                if pct_bucket > 0 and pct_bucket != job._last_notified_pct:
                    job._last_notified_pct = pct_bucket
                    notify_transfer_progress(
                        job.id, overall_pct, job.speed, job.eta,
                        job.files_done, job.files_total, job.current_file,
                    )

        proc.wait(timeout=60)

        if proc.returncode == 0:
            return True

        stderr = proc.stderr.read()
        job.error = stderr.strip()[:200] if stderr else "rsync hiba"
        return False

    except subprocess.TimeoutExpired:
        proc.kill()
        job.error = "Időtúllépés"
        return False
    except Exception as e:
        job.error = str(e)
        return False
