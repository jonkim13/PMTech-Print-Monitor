#!/bin/bash
# Start the Print Farm Monitor service
set -e
echo "Starting print-farm-monitor..."
sudo systemctl start print-farm-monitor
echo "Started."
sudo systemctl status print-farm-monitor --no-pager -l
