#!/bin/bash
# Uninstall the Print Farm Monitor systemd service
set -e

SERVICE_NAME="print-farm-monitor"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

echo "Uninstalling ${SERVICE_NAME} service..."

# Stop if running
sudo systemctl stop "${SERVICE_NAME}" 2>/dev/null || true

# Disable
sudo systemctl disable "${SERVICE_NAME}" 2>/dev/null || true

# Remove service file
if [ -f "${SERVICE_FILE}" ]; then
    sudo rm "${SERVICE_FILE}"
    echo "Removed ${SERVICE_FILE}"
fi

sudo systemctl daemon-reload

echo "Service uninstalled."
