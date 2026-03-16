#!/bin/bash
# ============================================================
# Print Farm Monitor - First-Time Pi Setup
# ============================================================
set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${PROJECT_DIR}/.env"
CONFIG_FILE="${PROJECT_DIR}/config.yaml"

echo "============================================"
echo "  Print Farm Monitor - First-Time Setup"
echo "============================================"
echo "  Project: ${PROJECT_DIR}"
echo "============================================"
echo ""

# --- Update apt ---
echo "[1/6] Updating apt..."
sudo apt-get update -y

# --- Install python3-pip and python3-venv ---
echo "[2/6] Installing Python dependencies..."
sudo apt-get install -y python3-pip python3-venv

# --- Create venv ---
echo "[3/6] Creating virtual environment..."
if [ ! -d "${PROJECT_DIR}/venv" ]; then
    python3 -m venv "${PROJECT_DIR}/venv"
    echo "  Created venv at ${PROJECT_DIR}/venv"
else
    echo "  venv already exists, skipping"
fi

# --- Install requirements ---
echo "[4/6] Installing Python packages..."
"${PROJECT_DIR}/venv/bin/pip" install -r "${PROJECT_DIR}/requirements.txt"

# --- Make deploy scripts executable ---
echo "[5/6] Making deploy scripts executable..."
chmod +x "${PROJECT_DIR}"/deploy/*.sh

# --- Create data directory ---
echo "[6/6] Setting up directories..."
mkdir -p "${PROJECT_DIR}/data"
mkdir -p "${PROJECT_DIR}/logs"

# --- Check .env ---
READY=true

if [ ! -f "${ENV_FILE}" ]; then
    echo ""
    echo "============================================"
    echo "  Creating template .env file"
    echo "============================================"
    cat > "${ENV_FILE}" <<'EOF'
# PrusaLink passwords for each printer
# Fill in your actual passwords before starting the service!
CORE_ONE_1_PASSWORD=replace-with-password
CORE_ONE_2_PASSWORD=replace-with-password
XL_1_PASSWORD=replace-with-password
XL_2_PASSWORD=replace-with-password
EOF
    echo "  Created ${ENV_FILE} with placeholder values."
    echo "  *** Edit this file with your actual printer passwords! ***"
    echo "      nano ${ENV_FILE}"
    READY=false
fi

# --- Check config.yaml ---
if [ ! -f "${CONFIG_FILE}" ]; then
    echo ""
    echo "============================================"
    echo "  WARNING: config.yaml not found!"
    echo "============================================"
    echo "  You need to create ${CONFIG_FILE}"
    echo "  with your printer details (IPs, names, etc.)"
    echo "  The repo should have included this file."
    READY=false
fi

# --- Setup service if ready ---
echo ""
if [ "${READY}" = true ]; then
    echo "============================================"
    echo "  .env and config.yaml found!"
    echo "  Setting up systemd service..."
    echo "============================================"
    "${PROJECT_DIR}/deploy/setup_service.sh"
else
    echo "============================================"
    echo "  Setup incomplete!"
    echo "============================================"
    echo "  Before starting the service, make sure:"
    echo "    1. .env has your real printer passwords"
    echo "    2. config.yaml has your printer details"
    echo ""
    echo "  Then run:"
    echo "    cd ${PROJECT_DIR}"
    echo "    deploy/setup_service.sh"
fi

# --- Print access info ---
IP_ADDR=$(hostname -I 2>/dev/null | awk '{print $1}')
echo ""
echo "============================================"
echo "  Once running, access the dashboard at:"
echo "  http://${IP_ADDR:-<your-pi-ip>}:5001"
echo "============================================"
