"""Flask API routes for Pi Archiver."""

from flask import Blueprint, jsonify, request

from app.config import (
    FILTER_PRESETS, load_config, save_config,
    get_archive_stats, clear_archive,
)
from app.storage import detect_usb_devices, list_files, mount_device, unmount_device
from app.transfer import (
    cancel_transfer, check_connection,
    get_transfer, get_transfers, start_transfer,
    remove_transfer, clear_finished_transfers,
)
from app.speedtest import get_speedtest_state, start_speedtest
from app.discord_notify import test_webhook

api = Blueprint("api", __name__, url_prefix="/api")


# --- USB Devices ---

@api.route("/devices")
def api_devices():
    return jsonify({"devices": detect_usb_devices()})


@api.route("/devices/mount", methods=["POST"])
def api_mount():
    data = request.get_json()
    device = data.get("device")
    if not device:
        return jsonify({"error": "Nincs megadva eszköz"}), 400
    mount_point = mount_device(device)
    if mount_point:
        return jsonify({"mounted": True, "mountpoint": mount_point})
    return jsonify({"error": "Nem sikerült mountolni az eszközt"}), 500


@api.route("/devices/unmount", methods=["POST"])
def api_unmount():
    data = request.get_json()
    mount_point = data.get("mountpoint")
    if not mount_point:
        return jsonify({"error": "Nincs megadva mount pont"}), 400
    if unmount_device(mount_point):
        return jsonify({"unmounted": True})
    return jsonify({"error": "Nem sikerült leválasztani"}), 500


# --- File Browser ---

@api.route("/files")
def api_files():
    mount_point = request.args.get("mount")
    path = request.args.get("path", "/")
    filter_type = request.args.get("filter", "all")
    if not mount_point:
        return jsonify({"error": "Nincs megadva mount pont"}), 400
    config = load_config()
    custom_ext = config["filters"].get("custom_extensions", [])
    return jsonify(list_files(mount_point, path, filter_type, custom_ext))


# --- Transfers ---

@api.route("/transfer", methods=["POST"])
def api_start_transfer():
    data = request.get_json()
    files = data.get("files", [])
    sizes = data.get("sizes", [])
    destination = data.get("destination", "")

    if not files:
        return jsonify({"error": "Nincsenek kijelölt fájlok"}), 400

    config = load_config()
    if not config["truenas"]["host"]:
        return jsonify({"error": "TrueNAS szerver nincs konfigurálva"}), 400

    if not destination:
        destination = config["transfer"].get("default_destination", "/")

    if len(sizes) != len(files):
        sizes = [0] * len(files)

    job_id = start_transfer(files, sizes, destination)
    return jsonify({"job_id": job_id, "status": "started"})


@api.route("/transfers")
def api_transfers():
    return jsonify({"transfers": get_transfers()})


@api.route("/transfer/<job_id>")
def api_transfer_status(job_id):
    job = get_transfer(job_id)
    if job:
        return jsonify(job)
    return jsonify({"error": "Transfer nem található"}), 404


@api.route("/transfer/<job_id>/cancel", methods=["POST"])
def api_cancel_transfer(job_id):
    if cancel_transfer(job_id):
        return jsonify({"cancelled": True})
    return jsonify({"error": "Nem lehet törölni"}), 400


@api.route("/transfer/<job_id>", methods=["DELETE"])
def api_remove_transfer(job_id):
    """Remove a finished/failed/cancelled transfer from the list."""
    if remove_transfer(job_id):
        return jsonify({"removed": True})
    return jsonify({"error": "Aktív átvitel nem törölhető"}), 400


@api.route("/transfers/clear", methods=["POST"])
def api_clear_transfers():
    """Remove all finished/failed/cancelled transfers."""
    count = clear_finished_transfers()
    return jsonify({"cleared": count})


# --- NAS Connection ---

@api.route("/nas/status")
def api_nas_status():
    return jsonify(check_connection())


# --- Speedtest ---

@api.route("/speedtest", methods=["POST"])
def api_speedtest_start():
    data = request.get_json() or {}
    pending = data.get("total_pending_bytes", 0)
    if start_speedtest(pending):
        return jsonify({"started": True})
    return jsonify({"error": "Speedtest már fut"}), 409


@api.route("/speedtest")
def api_speedtest_status():
    return jsonify(get_speedtest_state())


# --- Archive ---

@api.route("/archive/stats")
def api_archive_stats():
    return jsonify(get_archive_stats())


@api.route("/archive/clear", methods=["POST"])
def api_archive_clear():
    if clear_archive():
        return jsonify({"cleared": True})
    return jsonify({"error": "Nem sikerült törölni"}), 500


# --- Discord ---

@api.route("/discord/test", methods=["POST"])
def api_discord_test():
    data = request.get_json()
    url = data.get("webhook_url", "")
    if not url:
        return jsonify({"error": "Nincs megadva webhook URL"}), 400
    return jsonify(test_webhook(url))


# --- SSH Key Setup Helper ---

@api.route("/ssh/keygen", methods=["POST"])
def api_ssh_keygen():
    """Generate SSH key pair if not exists, return public key."""
    import subprocess
    config = load_config()
    key_path = config["transfer"].get("ssh_key_path", "/root/.ssh/id_ed25519")

    if not Path(key_path).exists():
        try:
            from pathlib import Path as P
            P(key_path).parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["ssh-keygen", "-t", "ed25519", "-f", key_path, "-N", ""],
                capture_output=True, text=True, timeout=10,
            )
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    try:
        from pathlib import Path as P
        pub_key = P(f"{key_path}.pub").read_text().strip()
        return jsonify({"public_key": pub_key, "key_path": key_path})
    except FileNotFoundError:
        return jsonify({"error": "Kulcs generálás sikertelen"}), 500


# --- Settings ---

@api.route("/settings", methods=["GET"])
def api_get_settings():
    config = load_config()
    safe_config = config.copy()
    # Mask passwords
    if safe_config.get("truenas", {}).get("password"):
        safe_config["truenas"]["password"] = "••••••••"
    if safe_config.get("transfer", {}).get("rsyncd_password"):
        safe_config["transfer"]["rsyncd_password"] = "••••••••"
    safe_config["filter_presets"] = {
        k: {"label": v["label"], "count": len(v["extensions"])}
        for k, v in FILTER_PRESETS.items()
    }
    return jsonify(safe_config)


@api.route("/settings", methods=["POST"])
def api_save_settings():
    data = request.get_json()
    config = load_config()

    if "truenas" in data:
        for key in ("host", "share", "path", "username"):
            if key in data["truenas"]:
                config["truenas"][key] = data["truenas"][key]
        if "password" in data["truenas"] and data["truenas"]["password"] != "••••••••":
            config["truenas"]["password"] = data["truenas"]["password"]

    if "transfer" in data:
        for key in ("mode", "default_destination", "bandwidth_limit",
                     "partial_transfer", "retry_attempts", "retry_delay",
                     "ssh_user", "ssh_key_path", "ssh_port", "ssh_remote_path",
                     "rsyncd_module", "rsyncd_port"):
            if key in data["transfer"]:
                config["transfer"][key] = data["transfer"][key]
        if "rsyncd_password" in data["transfer"] and data["transfer"]["rsyncd_password"] != "••••••••":
            config["transfer"]["rsyncd_password"] = data["transfer"]["rsyncd_password"]

    if "filters" in data:
        for key in ("default_filter", "custom_extensions"):
            if key in data["filters"]:
                config["filters"][key] = data["filters"][key]

    if "discord" in data:
        for key in ("webhook_url", "enabled", "notify_interval",
                     "notify_on_start", "notify_on_complete",
                     "notify_on_error", "notify_on_speedtest"):
            if key in data["discord"]:
                config["discord"][key] = data["discord"][key]

    if save_config(config):
        return jsonify({"saved": True})
    return jsonify({"error": "Nem sikerült menteni"}), 500


from pathlib import Path
