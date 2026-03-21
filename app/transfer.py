"""File transfer management: supports SMB, rsync-over-SSH, and rsyncd modes."""

import os
import re
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
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


def remove_transfer(job_id: str) -> bool:
    """Remove a finished/failed/cancelled transfer from the list."""
    with _transfer_lock:
        job = _transfers.get(job_id)
        if job and job.status in (TransferStatus.COMPLETED, TransferStatus.FAILED, TransferStatus.CANCELLED):
            del _transfers[job_id]
            return True
    return False


def clear_finished_transfers() -> int:
    """Remove all finished/failed/cancelled transfers. Returns count removed."""
    with _transfer_lock:
        to_remove = [
            k for k, v in _transfers.items()
            if v.status in (TransferStatus.COMPLETED, TransferStatus.FAILED, TransferStatus.CANCELLED)
        ]
        for k in to_remove:
            del _transfers[k]
        return len(to_remove)


def start_transfer(source_files: list[str], file_sizes: list[int], destination: str) -> str:
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


# ==================== Connection Checks ====================


def check_connection() -> dict:
    """Check NAS connection based on current transfer mode."""
    config = load_config()
    mode = config["transfer"].get("mode", "ssh")
    host = config["truenas"]["host"]

    if not host:
        return {"connected": False, "error": "TrueNAS nincs konfigurálva", "mode": mode}

    if mode == "smb":
        return _check_smb(config)
    elif mode == "ssh":
        return _check_ssh(config)
    elif mode == "rsyncd":
        return _check_rsyncd(config)
    return {"connected": False, "error": f"Ismeretlen mód: {mode}", "mode": mode}


def _check_smb(config: dict) -> dict:
    nas = config["truenas"]
    if not nas["share"]:
        return {"connected": False, "error": "SMB share neve nincs megadva", "mode": "smb"}
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
            return {"connected": True, "host": nas["host"], "share": nas["share"], "mode": "smb"}
        return {"connected": False, "error": result.stderr.strip()[:100], "mode": "smb"}
    except subprocess.TimeoutExpired:
        return {"connected": False, "error": "Időtúllépés", "mode": "smb"}
    except Exception as e:
        return {"connected": False, "error": str(e), "mode": "smb"}


def _check_ssh(config: dict) -> dict:
    t = config["transfer"]
    host = config["truenas"]["host"]
    user = t.get("ssh_user", "root")
    key = t.get("ssh_key_path", "")
    port = t.get("ssh_port", 22)

    if not key or not Path(key).exists():
        return {"connected": False, "error": f"SSH kulcs nem található: {key}", "mode": "ssh"}

    try:
        cmd = [
            "ssh", "-i", key, "-p", str(port),
            "-o", "ConnectTimeout=5",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "BatchMode=yes",
            f"{user}@{host}", "echo ok",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            return {"connected": True, "host": host, "user": user, "mode": "ssh"}
        return {"connected": False, "error": result.stderr.strip()[:100], "mode": "ssh"}
    except subprocess.TimeoutExpired:
        return {"connected": False, "error": "SSH időtúllépés", "mode": "ssh"}
    except Exception as e:
        return {"connected": False, "error": str(e), "mode": "ssh"}


def _check_rsyncd(config: dict) -> dict:
    t = config["transfer"]
    host = config["truenas"]["host"]
    module = t.get("rsyncd_module", "")
    port = t.get("rsyncd_port", 873)

    if not module:
        return {"connected": False, "error": "rsyncd modul nincs megadva", "mode": "rsyncd"}

    try:
        cmd = ["rsync", "--port", str(port), "--list-only", f"rsync://{host}/{module}/"]
        env = os.environ.copy()
        pwd = t.get("rsyncd_password", "")
        if pwd:
            env["RSYNC_PASSWORD"] = pwd
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10, env=env)
        if result.returncode == 0:
            return {"connected": True, "host": host, "module": module, "mode": "rsyncd"}
        return {"connected": False, "error": result.stderr.strip()[:100], "mode": "rsyncd"}
    except subprocess.TimeoutExpired:
        return {"connected": False, "error": "rsyncd időtúllépés", "mode": "rsyncd"}
    except Exception as e:
        return {"connected": False, "error": str(e), "mode": "rsyncd"}


# ==================== SMB Mount (only for SMB mode) ====================


def mount_smb() -> Optional[str]:
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
        result = subprocess.run(["mountpoint", "-q", path], capture_output=True, timeout=5)
        return result.returncode == 0
    except Exception:
        return False


# ==================== Remote Folder Operations ====================


def list_remote_folders(subpath: str = "") -> dict:
    """List folders on the remote NAS at the base path + subpath."""
    config = load_config()
    mode = config["transfer"].get("mode", "ssh")
    host = config["truenas"]["host"]
    t = config["transfer"]

    if not host:
        return {"error": "TrueNAS nincs konfigurálva", "folders": []}

    try:
        if mode == "ssh":
            user = t.get("ssh_user", "root")
            key = t.get("ssh_key_path", "/root/.ssh/id_ed25519")
            port = t.get("ssh_port", 22)
            remote_base = t.get("ssh_remote_path", "").rstrip("/")
            target = remote_base
            if subpath.strip("/"):
                target = f"{remote_base}/{subpath.strip('/')}"

            result = subprocess.run(
                [
                    "ssh", "-i", key, "-p", str(port),
                    "-o", "StrictHostKeyChecking=accept-new",
                    "-o", "BatchMode=yes",
                    "-o", "ConnectTimeout=5",
                    f"{user}@{host}",
                    f"find '{target}' -maxdepth 1 -mindepth 1 -type d -printf '%f\\n' 2>/dev/null | sort",
                ],
                capture_output=True, text=True, timeout=15,
            )
            folders = [f for f in result.stdout.strip().split("\n") if f]
            return {"folders": folders, "current_path": subpath or "/"}

        elif mode == "rsyncd":
            module = t.get("rsyncd_module", "")
            port_num = t.get("rsyncd_port", 873)
            env = os.environ.copy()
            pwd = t.get("rsyncd_password", "")
            if pwd:
                env["RSYNC_PASSWORD"] = pwd

            rsync_path = f"rsync://{host}:{port_num}/{module}/"
            if subpath.strip("/"):
                rsync_path += subpath.strip("/") + "/"

            result = subprocess.run(
                ["rsync", "--list-only", rsync_path],
                capture_output=True, text=True, timeout=15, env=env,
            )
            folders = []
            for line in result.stdout.strip().split("\n"):
                if line.startswith("d") and len(line) > 40:
                    name = line.split(None, 4)[-1].rstrip("/")
                    if name and name != ".":
                        folders.append(name)
            return {"folders": sorted(folders), "current_path": subpath or "/"}

        elif mode == "smb":
            mp = mount_smb()
            if not mp:
                return {"error": "SMB mount hiba", "folders": []}
            nas_base = config["truenas"].get("path", "/").strip("/")
            target = Path(mp)
            if nas_base:
                target = target / nas_base
            if subpath.strip("/"):
                target = target / subpath.strip("/")
            if not target.exists():
                return {"folders": [], "current_path": subpath or "/"}
            folders = sorted([d.name for d in target.iterdir() if d.is_dir() and not d.name.startswith(".")])
            return {"folders": folders, "current_path": subpath or "/"}

    except Exception as e:
        return {"error": str(e), "folders": []}

    return {"error": "Ismeretlen mód", "folders": []}


def create_remote_folder(folder_name: str, subpath: str = "") -> dict:
    """Create a new folder on the remote NAS."""
    config = load_config()
    mode = config["transfer"].get("mode", "ssh")
    host = config["truenas"]["host"]
    t = config["transfer"]

    # Sanitize folder name
    folder_name = folder_name.strip().replace("/", "_").replace("\\", "_")
    if not folder_name:
        return {"error": "Üres mappanév"}

    try:
        if mode == "ssh":
            user = t.get("ssh_user", "root")
            key = t.get("ssh_key_path", "/root/.ssh/id_ed25519")
            port = t.get("ssh_port", 22)
            remote_base = t.get("ssh_remote_path", "").rstrip("/")
            target = remote_base
            if subpath.strip("/"):
                target = f"{remote_base}/{subpath.strip('/')}"
            full_path = f"{target}/{folder_name}"

            result = subprocess.run(
                [
                    "ssh", "-i", key, "-p", str(port),
                    "-o", "StrictHostKeyChecking=accept-new",
                    "-o", "BatchMode=yes",
                    f"{user}@{host}",
                    f"mkdir -p '{full_path}'",
                ],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                return {"created": True, "name": folder_name}
            return {"error": result.stderr.strip()[:100]}

        elif mode == "smb":
            mp = mount_smb()
            if not mp:
                return {"error": "SMB mount hiba"}
            nas_base = config["truenas"].get("path", "/").strip("/")
            target = Path(mp)
            if nas_base:
                target = target / nas_base
            if subpath.strip("/"):
                target = target / subpath.strip("/")
            new_dir = target / folder_name
            new_dir.mkdir(parents=True, exist_ok=True)
            return {"created": True, "name": folder_name}

        elif mode == "rsyncd":
            # rsyncd protocol has no mkdir — use SSH just for this operation
            user = t.get("ssh_user", "root")
            key = t.get("ssh_key_path", "/root/.ssh/id_ed25519")
            port = t.get("ssh_port", 22)
            remote_base = t.get("ssh_remote_path", "").rstrip("/")

            if not key or not Path(key).exists():
                return {"error": f"SSH kulcs nem található: {key} (mappalétrehozáshoz szükséges)"}

            target = remote_base
            if subpath.strip("/"):
                target = f"{remote_base}/{subpath.strip('/')}"
            full_path = f"{target}/{folder_name}"

            result = subprocess.run(
                [
                    "ssh", "-i", key, "-p", str(port),
                    "-o", "StrictHostKeyChecking=accept-new",
                    "-o", "BatchMode=yes",
                    "-o", "ConnectTimeout=5",
                    f"{user}@{host}",
                    f"mkdir -p '{full_path}'",
                ],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                return {"created": True, "name": folder_name}
            return {"error": result.stderr.strip()[:100]}

    except Exception as e:
        return {"error": str(e)}

    return {"error": "Ismeretlen mód"}


# ==================== Rsync Destination Builders ====================


def _build_rsync_dest(config: dict, destination: str) -> tuple[list[str], str, dict]:
    """Build rsync command prefix, destination string, and env based on mode.

    Returns: (extra_rsync_args, rsync_destination_path, env_dict)
    """
    mode = config["transfer"].get("mode", "ssh")
    t = config["transfer"]
    host = config["truenas"]["host"]

    env = os.environ.copy()

    if mode == "ssh":
        user = t.get("ssh_user", "root")
        key = t.get("ssh_key_path", "/root/.ssh/id_ed25519")
        port = t.get("ssh_port", 22)
        remote_base = t.get("ssh_remote_path", "").rstrip("/")
        dest_subpath = destination.strip("/")

        remote_path = remote_base
        if dest_subpath:
            remote_path = f"{remote_base}/{dest_subpath}"

        ssh_cmd = (
            f"ssh -i {key} -p {port}"
            f" -o StrictHostKeyChecking=accept-new"
            f" -o ConnectTimeout=10"
            f" -o ServerAliveInterval=5"
            f" -o ServerAliveCountMax=3"
        )
        extra_args = ["-e", ssh_cmd]
        rsync_dest = f"{user}@{host}:{remote_path}/"
        return extra_args, rsync_dest, env

    elif mode == "rsyncd":
        module = t.get("rsyncd_module", "")
        port = t.get("rsyncd_port", 873)
        dest_subpath = destination.strip("/")

        rsync_dest = f"rsync://{host}:{port}/{module}/"
        if dest_subpath:
            rsync_dest = f"rsync://{host}:{port}/{module}/{dest_subpath}/"

        pwd = t.get("rsyncd_password", "")
        if pwd:
            env["RSYNC_PASSWORD"] = pwd

        return [], rsync_dest, env

    elif mode == "smb":
        # SMB: rsync to local mount point
        nas_base_path = config["truenas"].get("path", "/").strip("/")
        dest_subpath = destination.strip("/")

        dest_dir = Path(SMB_MOUNT_POINT)
        if nas_base_path:
            dest_dir = dest_dir / nas_base_path
        if dest_subpath:
            dest_dir = dest_dir / dest_subpath
        dest_dir.mkdir(parents=True, exist_ok=True)

        return [], str(dest_dir) + "/", env

    return [], "", env


# ==================== Transfer Execution ====================


def _run_transfer(job: TransferJob):
    from app.discord_notify import (
        notify_transfer_start, notify_transfer_complete, notify_transfer_error,
    )

    try:
        job.started_at = time.time()
        config = load_config()
        mode = config["transfer"].get("mode", "ssh")

        # SMB mode: mount first
        if mode == "smb":
            job.status = TransferStatus.MOUNTING
            if not mount_smb():
                job.status = TransferStatus.FAILED
                job.error = "Nem sikerült csatlakozni a TrueNAS-hoz (SMB mount hiba)"
                notify_transfer_error(job.id, job.error, 0, job.files_total)
                return

        if job.status == TransferStatus.CANCELLED:
            return

        # Build rsync destination
        extra_args, rsync_dest, rsync_env = _build_rsync_dest(config, job.destination)

        # Notify start
        notify_transfer_start(job.id, job.files_total, job.total_bytes, job.destination)

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

            success = _rsync_with_retry(
                job, str(source_path), rsync_dest, extra_args, rsync_env
            )

            if success:
                stat = source_path.stat()
                mark_archived(source_path.name, stat.st_size, stat.st_mtime, job.destination)
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


def _rsync_with_retry(job, source, rsync_dest, extra_args, rsync_env) -> bool:
    config = load_config()
    max_retries = config["transfer"].get("retry_attempts", 5)
    retry_delay = config["transfer"].get("retry_delay", 3)
    mode = config["transfer"].get("mode", "ssh")

    for attempt in range(max_retries + 1):
        if job.status == TransferStatus.CANCELLED:
            return False

        success = _rsync_file(job, source, rsync_dest, extra_args, rsync_env)
        if success:
            return True

        if attempt < max_retries:
            job.retry_count += 1
            job.error = f"Újrapróbálkozás ({attempt + 1}/{max_retries})..."
            print(f"Retry {attempt + 1}/{max_retries} for {source}, waiting {retry_delay}s")

            for _ in range(retry_delay):
                if job.status == TransferStatus.CANCELLED:
                    return False
                time.sleep(1)

            # SMB: re-check mount
            if mode == "smb" and not _is_mounted(SMB_MOUNT_POINT):
                print("SMB disconnected, remounting...")
                if not mount_smb():
                    job.error = "NAS kapcsolat megszakadt, újracsatlakozás sikertelen"
                    return False

    return False


def _rsync_file(job, source, rsync_dest, extra_args, rsync_env) -> bool:
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
        "--inplace",
        "--no-perms",
        "--no-owner",
        "--no-group",
    ]

    # --contimeout only works with rsyncd, not SSH
    mode = config["transfer"].get("mode", "ssh")
    if mode == "rsyncd":
        cmd.append("--contimeout=15")

    if transfer_cfg.get("partial_transfer", True):
        cmd.append("--partial")

    bw_limit = transfer_cfg.get("bandwidth_limit", 0)
    if bw_limit > 0:
        cmd.extend(["--bwlimit", str(bw_limit)])

    # Add mode-specific args (e.g. -e ssh for SSH mode)
    cmd.extend(extra_args)

    # Source file, then destination
    cmd.extend([source, rsync_dest])

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1, env=rsync_env,
        )

        progress_re = re.compile(r"[\d,]+\s+(\d+)%\s+([\d.]+\w+/s)\s+([\d:]+)")

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

                elapsed = time.time() - job.started_at
                if elapsed > 0 and job.bytes_transferred > 0:
                    job.avg_speed = job.bytes_transferred / elapsed

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
