#!/bin/bash
set -e

echo "🔄 Pi Archiver - Frissítés"

if [ "$EUID" -ne 0 ]; then
    echo "❌ Root jogosultság szükséges: sudo ./update.sh"
    exit 1
fi

# Update Python packages if requirements changed
pip3 install --break-system-packages -r requirements.txt -q

# Copy updated files
cp -r app/ /opt/pi-archiver/
cp requirements.txt /opt/pi-archiver/

# Update systemd service if changed
cp systemd/pi-archiver.service /etc/systemd/system/
systemctl daemon-reload

# Restart service
systemctl restart pi-archiver

echo "✅ Frissítés kész! Szolgáltatás újraindítva."
