#!/bin/bash
set -e

echo "========================================="
echo "  Pi Archiver - Telepítés"
echo "========================================="

# Check root
if [ "$EUID" -ne 0 ]; then
    echo "❌ Root jogosultság szükséges: sudo ./install.sh"
    exit 1
fi

# Install system dependencies
echo "📦 Rendszercsomagok telepítése..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip rsync cifs-utils smbclient

# Install Python packages
echo "🐍 Python csomagok telepítése..."
pip3 install --break-system-packages -r requirements.txt

# Create directories
echo "📁 Könyvtárak létrehozása..."
mkdir -p /etc/pi-archiver
mkdir -p /mnt/pi-archiver
mkdir -p /mnt/pi-archiver-nas

# Copy app to /opt
echo "📋 Alkalmazás másolása /opt/pi-archiver alá..."
mkdir -p /opt/pi-archiver
cp -r app/ /opt/pi-archiver/
cp requirements.txt /opt/pi-archiver/

# Install systemd service
echo "⚙️ Systemd service telepítése..."
cp systemd/pi-archiver.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable pi-archiver
systemctl start pi-archiver

echo ""
echo "========================================="
echo "  ✅ Telepítés kész!"
echo "========================================="
echo ""
echo "  Webes felület: http://$(hostname -I | awk '{print $1}'):5000"
echo "  Tailscale-en:  http://$(tailscale ip -4 2>/dev/null || echo 'N/A'):5000"
echo ""
echo "  Parancsok:"
echo "    Állapot:    sudo systemctl status pi-archiver"
echo "    Napló:      sudo journalctl -u pi-archiver -f"
echo "    Újraindítás: sudo systemctl restart pi-archiver"
echo "    Frissítés:  cd $(pwd) && git pull && sudo ./update.sh"
echo ""
