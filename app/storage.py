"""USB storage detection and mount management."""

import json
import subprocess
from pathlib import Path
from typing import Optional

from app.config import load_config, is_archived


def detect_usb_devices() -> list[dict]:
    """Detect USB storage devices using lsblk."""
    try:
        result = subprocess.run(
            [
                "lsblk", "-J", "-o",
                "NAME,SIZE,FSTYPE,LABEL,MOUNTPOINT,TRAN,HOTPLUG,TYPE",
            ],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return []

        data = json.loads(result.stdout)
        devices = []

        for dev in data.get("blockdevices", []):
            if dev.get("tran") != "usb" and not dev.get("hotplug"):
                continue
            if dev.get("type") != "disk":
                continue

            children = dev.get("children", [])
            if children:
                for part in children:
                    if part.get("fstype"):
                        devices.append({
                            "device": f"/dev/{part['name']}",
                            "parent": f"/dev/{dev['name']}",
                            "size": part.get("size", "?"),
                            "filesystem": part.get("fstype", ""),
                            "label": part.get("label", ""),
                            "mountpoint": part.get("mountpoint"),
                            "mounted": part.get("mountpoint") is not None,
                        })
            elif dev.get("fstype"):
                devices.append({
                    "device": f"/dev/{dev['name']}",
                    "parent": None,
                    "size": dev.get("size", "?"),
                    "filesystem": dev.get("fstype", ""),
                    "label": dev.get("label", ""),
                    "mountpoint": dev.get("mountpoint"),
                    "mounted": dev.get("mountpoint") is not None,
                })

        return devices
    except Exception as e:
        print(f"USB detection error: {e}")
        return []


def mount_device(device_path: str) -> Optional[str]:
    """Mount a USB device, return mount point or None."""
    config = load_config()
    mount_base = Path(config["usb"]["mount_base"])
    dev_name = Path(device_path).name
    mount_point = mount_base / dev_name
    mount_point.mkdir(parents=True, exist_ok=True)

    existing = _get_mountpoint(device_path)
    if existing:
        return existing

    try:
        result = subprocess.run(
            ["mount", "-o", "ro,noexec,nosuid", device_path, str(mount_point)],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            return str(mount_point)

        result = subprocess.run(
            ["mount", "-o", "noexec,nosuid", device_path, str(mount_point)],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            return str(mount_point)

        print(f"Mount error: {result.stderr}")
        return None
    except Exception as e:
        print(f"Mount exception: {e}")
        return None


def unmount_device(mount_point: str) -> bool:
    try:
        result = subprocess.run(
            ["umount", mount_point],
            capture_output=True, text=True, timeout=15,
        )
        return result.returncode == 0
    except Exception:
        return False


def _get_mountpoint(device_path: str) -> Optional[str]:
    try:
        result = subprocess.run(
            ["findmnt", "-n", "-o", "TARGET", device_path],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return None


def list_files(
    mount_point: str,
    path: str = "/",
    filter_type: str = "all",
    custom_extensions: Optional[list[str]] = None,
) -> dict:
    """List files at a given path with optional filtering.
    Includes archive status for each file.
    """
    from app.config import FILTER_PRESETS

    base = Path(mount_point)
    target = (base / path.lstrip("/")).resolve()

    if not str(target).startswith(str(base)):
        return {"error": "Invalid path", "directories": [], "files": []}

    if not target.exists():
        return {"error": "Path not found", "directories": [], "files": []}

    allowed_ext = set()
    if filter_type != "all":
        if filter_type == "custom" and custom_extensions:
            allowed_ext = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in custom_extensions}
        elif filter_type in FILTER_PRESETS:
            allowed_ext = set(FILTER_PRESETS[filter_type]["extensions"])

    directories = []
    files = []
    archived_count = 0

    try:
        for entry in sorted(target.iterdir()):
            if entry.name.startswith("."):
                continue

            if entry.is_dir():
                try:
                    item_count = sum(1 for _ in entry.iterdir())
                except PermissionError:
                    item_count = 0
                directories.append({
                    "name": entry.name,
                    "path": str(entry.relative_to(base)),
                    "items": item_count,
                })
            elif entry.is_file():
                ext = entry.suffix.lower()
                if allowed_ext and ext not in allowed_ext:
                    continue

                try:
                    stat = entry.stat()
                    archived = is_archived(entry.name, stat.st_size, stat.st_mtime)
                    if archived:
                        archived_count += 1

                    files.append({
                        "name": entry.name,
                        "path": str(entry.relative_to(base)),
                        "size": stat.st_size,
                        "size_human": _human_size(stat.st_size),
                        "extension": ext,
                        "modified": stat.st_mtime,
                        "archived": archived,
                    })
                except (PermissionError, OSError):
                    continue
    except PermissionError:
        return {"error": "Permission denied", "directories": [], "files": []}

    return {
        "current_path": str(target.relative_to(base)),
        "directories": directories,
        "files": files,
        "total_files": len(files),
        "archived_count": archived_count,
    }


def _human_size(size_bytes: int) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}" if unit != "B" else f"{size_bytes} B"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"
