#!/bin/bash
# Show Print Farm Monitor service status and recent logs
echo "============================================"
echo "  Print Farm Monitor - Status"
echo "============================================"
sudo systemctl status print-farm-monitor --no-pager -l
echo ""
echo "--- Recent Logs (last 30 lines) ---"
journalctl -u print-farm-monitor -n 30 --no-pager
