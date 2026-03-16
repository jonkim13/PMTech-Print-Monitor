#!/bin/bash
# Stop the Print Farm Monitor service
set -e
echo "Stopping print-farm-monitor..."
sudo systemctl stop print-farm-monitor
echo "Stopped."
