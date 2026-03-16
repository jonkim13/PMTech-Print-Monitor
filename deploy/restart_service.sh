#!/bin/bash
# Restart the Print Farm Monitor service
set -e
echo "Restarting print-farm-monitor..."
sudo systemctl restart print-farm-monitor
echo "Restarted."
sudo systemctl status print-farm-monitor --no-pager -l
