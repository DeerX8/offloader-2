"""Configuration management for Pi Archiver."""

import json
import os
import time
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("PI_ARCHIVER_CONFIG_DIR", "/etc/pi-archiver"))
CONFIG_FILE = CONFIG_DIR / "config.json"
ARCHIVE_FILE = CONFIG_DIR / "archive.json"

DEFAULT_CONFIG = {
    "truenas": {
        "host": "",
        "share": "",
        "path": "/",
        "username": "",
        "password": "",
    },
    "transfer": {
        "default_destination": "/",
        "bandwidth_limit": 0,
        "partial_transfer": True,
        "retry_attempts": 5,
        "retry_delay": 3,
    },
    "filters": {
        "default_filter": "all",
        "custom_extensions": [],
    },
    "usb": {
        "auto_mount": True,
        "mount_base": "/mnt/pi-archiver",
    },
    "discord": {
        "webhook_url": "",
        "enabled": False,
        "notify_interval": 10,
        "notify_on_start": True,
        "notify_on_complete": True,
        "notify_on_error": True,
        "notify_on_speedtest": True,
    },
}

FILTER_PRESETS = {
    "all": {"label": "Minden fájl", "extensions": []},
    "photo": {
        "label": "Fotók",
        "extensions": [
            ".jpg", ".jpeg", ".png", ".tiff", ".tif",
            ".raw", ".cr2", ".cr3", ".nef", ".arw",
            ".orf", ".rw2", ".dng", ".heic", ".heif",
        ],
    },
    "video": {
        "label": "Videók",
        "extensions": [
            ".mp4", ".mov", ".avi", ".mkv", ".mts",
            ".m2ts", ".mxf", ".braw", ".prores",
        ],
    },
    "photo_video": {
        "label": "Fotó + Videó",
        "extensions": [],
    },
}

FILTER_PRESETS["photo_video"]["extensions"] = (
    FILTER_PRESETS["photo"]["extensions"] + FILTER_PRESETS["video"]["extensions"]
)


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r") as f:
                saved = json.load(f)
            return _deep_merge(DEFAULT_CONFIG, saved)
        except (json.JSONDecodeError, IOError):
            pass
    return DEFAULT_CONFIG.copy()


def save_config(config: dict) -> bool:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        return True
    except IOError as e:
        print(f"Config save error: {e}")
        return False


def _deep_merge(default: dict, override: dict) -> dict:
    result = default.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


# ==================== Archive Tracking ====================


def load_archive() -> dict:
    """Load archive database.
    Format: { "fingerprint": { name, size, mtime, archived_at, destination } }
    """
    if ARCHIVE_FILE.exists():
        try:
            with open(ARCHIVE_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_archive(archive: dict) -> bool:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(ARCHIVE_FILE, "w") as f:
            json.dump(archive, f, indent=2, ensure_ascii=False)
        return True
    except IOError as e:
        print(f"Archive save error: {e}")
        return False


def file_fingerprint(name: str, size: int, mtime: float) -> str:
    """Unique fingerprint: name + size + modification time."""
    return f"{name}|{size}|{int(mtime)}"


def is_archived(name: str, size: int, mtime: float) -> bool:
    archive = load_archive()
    fp = file_fingerprint(name, size, mtime)
    return fp in archive


def mark_archived(name: str, size: int, mtime: float, destination: str) -> None:
    archive = load_archive()
    fp = file_fingerprint(name, size, mtime)
    archive[fp] = {
        "name": name,
        "size": size,
        "mtime": mtime,
        "destination": destination,
        "archived_at": time.time(),
    }
    save_archive(archive)


def get_archive_stats() -> dict:
    archive = load_archive()
    total_size = sum(entry.get("size", 0) for entry in archive.values())
    return {
        "total_files": len(archive),
        "total_size": total_size,
        "total_size_human": _human_size(total_size),
    }


def clear_archive() -> bool:
    return save_archive({})


def _human_size(size_bytes: int) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}" if unit != "B" else f"{size_bytes} B"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"
