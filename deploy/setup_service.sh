#!/bin/bash
# ============================================================
# Setup systemd service for Print Farm Monitor
# ============================================================
set -e

SERVICE_NAME="print-farm-monitor"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CURRENT_USER="$(whoami)"
VENV_PYTHON="${PROJECT_DIR}/venv/bin/python3"
ENV_FILE="${PROJECT_DIR}/.env"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

echo "============================================"
echo "  Print Farm Monitor - Service Setup"
echo "============================================"
echo "  User:        ${CURRENT_USER}"
echo "  Project:     ${PROJECT_DIR}"
echo "  Python:      ${VENV_PYTHON}"
echo "  Env file:    ${ENV_FILE}"
echo "============================================"

# Verify venv exists
if [ ! -f "${VENV_PYTHON}" ]; then
    echo "ERROR: Virtual environment not found at ${VENV_PYTHON}"
    echo "Run deploy/install.sh first."
    exit 1
fi

# Verify .env exists
if [ ! -f "${ENV_FILE}" ]; then
    echo "ERROR: .env file not found at ${ENV_FILE}"
    echo "Create it with your printer passwords before setting up the service."
    exit 1
fi

# Create logs directory
mkdir -p "${PROJECT_DIR}/logs"

# Create systemd service file
echo "Creating systemd service..."
sudo tee "${SERVICE_FILE}" > /dev/null <<EOF
[Unit]
Description=Print Farm Monitor
After=network.target

[Service]
Type=simple
User=${CURRENT_USER}
WorkingDirectory=${PROJECT_DIR}
EnvironmentFile=${ENV_FILE}
Environment=PYTHONUNBUFFERED=1
ExecStart=${VENV_PYTHON} server.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# Reload systemd, enable and start
sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"
sudo systemctl start "${SERVICE_NAME}"

echo ""
echo "============================================"
echo "  Service installed and started!"
echo "============================================"
sudo systemctl status "${SERVICE_NAME}" --no-pager -l

# Print access URL
IP_ADDR=$(hostname -I 2>/dev/null | awk '{print $1}')
echo ""
echo "============================================"
echo "  Dashboard: http://${IP_ADDR:-localhost}:5001"
echo "============================================"
