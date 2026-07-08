#!/bin/bash
set -e

echo "=== LD2420 HDMI Control — Pi Zero 2W Install ==="

# 1. Install Python dependency
echo "[1/4] Installing pyserial..."
pip install pyserial

# 2. Copy script
echo "[2/4] Copying script to /home/pi/..."
cp ld2420_hdmi_control.py /home/pi/
chmod +x /home/pi/ld2420_hdmi_control.py

# 3. Install systemd service
echo "[3/4] Installing systemd service..."
cp ld2420-hdmi.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable ld2420-hdmi
systemctl start ld2420-hdmi

# 4. Verify
echo "[4/4] Checking status..."
sleep 2
systemctl status ld2420-hdmi --no-pager

echo ""
echo "=== Done ==="
echo "Logs: journalctl -u ld2420-hdmi -f"
echo "Config: edit /home/pi/ld2420_hdmi_control.py, then: sudo systemctl restart ld2420-hdmi"
