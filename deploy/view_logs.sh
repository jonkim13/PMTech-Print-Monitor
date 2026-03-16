#!/bin/bash
# Tail live logs for Print Farm Monitor
echo "Tailing print-farm-monitor logs (Ctrl+C to stop)..."
echo ""
journalctl -u print-farm-monitor -f
