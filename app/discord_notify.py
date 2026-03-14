"""Discord webhook notifications for Pi Archiver."""

import time
import threading
import requests
from app.config import load_config, _human_size


def _send_webhook(embed: dict) -> bool:
    """Send a Discord webhook message with embed."""
    config = load_config()
    discord = config.get("discord", {})

    if not discord.get("enabled") or not discord.get("webhook_url"):
        return False

    try:
        payload = {
            "username": "📦 Pi Archiver",
            "embeds": [embed],
        }
        resp = requests.post(
            discord["webhook_url"],
            json=payload,
            timeout=10,
        )
        return resp.status_code in (200, 204)
    except Exception as e:
        print(f"Discord webhook error: {e}")
        return False


def notify_transfer_start(job_id: str, file_count: int, total_size: int, destination: str,
                          estimated_eta: str = ""):
    """Notify that a transfer has started."""
    config = load_config()
    if not config["discord"].get("notify_on_start"):
        return

    fields = [
        {"name": "📁 Fájlok", "value": str(file_count), "inline": True},
        {"name": "💾 Méret", "value": _human_size(total_size), "inline": True},
        {"name": "📍 Cél", "value": destination or "/", "inline": True},
    ]

    if estimated_eta:
        fields.append({"name": "⏱️ Becsült idő", "value": estimated_eta, "inline": True})

    embed = {
        "title": "🚀 Másolás indítva",
        "color": 3447003,  # Blue
        "fields": fields,
        "footer": {"text": f"Job: {job_id}"},
        "timestamp": _iso_now(),
    }
    threading.Thread(target=_send_webhook, args=(embed,), daemon=True).start()


def notify_transfer_progress(job_id: str, progress: float, speed: str, eta: str,
                             files_done: int, files_total: int, current_file: str):
    """Notify transfer progress at configured intervals."""
    embed = {
        "title": f"📤 Másolás: {progress:.0f}%",
        "color": 1752220,  # Green-ish
        "fields": [
            {"name": "📊 Haladás", "value": f"{'█' * int(progress / 5)}{'░' * (20 - int(progress / 5))} {progress:.1f}%", "inline": False},
            {"name": "📁 Fájlok", "value": f"{files_done}/{files_total}", "inline": True},
            {"name": "🚀 Sebesség", "value": speed or "—", "inline": True},
            {"name": "⏱️ ETA", "value": eta or "—", "inline": True},
            {"name": "📄 Jelenlegi", "value": current_file[:50] or "—", "inline": False},
        ],
        "footer": {"text": f"Job: {job_id}"},
        "timestamp": _iso_now(),
    }
    threading.Thread(target=_send_webhook, args=(embed,), daemon=True).start()


def notify_transfer_complete(job_id: str, files_total: int, total_size: int,
                             duration: float, avg_speed: float):
    """Notify that a transfer completed."""
    config = load_config()
    if not config["discord"].get("notify_on_complete"):
        return

    mins = int(duration // 60)
    secs = int(duration % 60)
    duration_str = f"{mins}p {secs}mp" if mins > 0 else f"{secs}mp"

    embed = {
        "title": "✅ Másolás kész!",
        "color": 5763719,  # Green
        "fields": [
            {"name": "📁 Fájlok", "value": str(files_total), "inline": True},
            {"name": "💾 Méret", "value": _human_size(total_size), "inline": True},
            {"name": "⏱️ Idő", "value": duration_str, "inline": True},
            {"name": "🚀 Átlag sebesség", "value": f"{_human_size(int(avg_speed))}/s", "inline": True},
        ],
        "footer": {"text": f"Job: {job_id}"},
        "timestamp": _iso_now(),
    }
    threading.Thread(target=_send_webhook, args=(embed,), daemon=True).start()


def notify_transfer_error(job_id: str, error: str, files_done: int, files_total: int):
    """Notify that a transfer failed."""
    config = load_config()
    if not config["discord"].get("notify_on_error"):
        return

    embed = {
        "title": "❌ Másolás hiba",
        "color": 15548997,  # Red
        "fields": [
            {"name": "📁 Haladás", "value": f"{files_done}/{files_total} fájl", "inline": True},
            {"name": "⚠️ Hiba", "value": error[:200] or "Ismeretlen hiba", "inline": False},
        ],
        "footer": {"text": f"Job: {job_id}"},
        "timestamp": _iso_now(),
    }
    threading.Thread(target=_send_webhook, args=(embed,), daemon=True).start()


def notify_speedtest_result(write_speed: float, estimated_eta: str, total_size: int):
    """Notify speedtest result."""
    config = load_config()
    if not config["discord"].get("notify_on_speedtest"):
        return

    embed = {
        "title": "🏎️ Speedtest eredmény",
        "color": 16776960,  # Yellow
        "fields": [
            {"name": "📝 Írási sebesség", "value": f"{_human_size(int(write_speed))}/s", "inline": True},
            {"name": "💾 Várakozó adat", "value": _human_size(total_size), "inline": True},
            {"name": "⏱️ Becsült ETA", "value": estimated_eta, "inline": True},
        ],
        "timestamp": _iso_now(),
    }
    threading.Thread(target=_send_webhook, args=(embed,), daemon=True).start()


def test_webhook(url: str) -> dict:
    """Send a test message to verify webhook URL."""
    try:
        payload = {
            "username": "📦 Pi Archiver",
            "embeds": [{
                "title": "✅ Webhook teszt sikeres!",
                "description": "Pi Archiver Discord értesítések működnek.",
                "color": 5763719,
                "timestamp": _iso_now(),
            }],
        }
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code in (200, 204):
            return {"success": True}
        return {"success": False, "error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
