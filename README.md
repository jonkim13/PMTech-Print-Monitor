# Print Farm Monitor

Central monitoring server for the Prusa print farm.
Polls all printers via PrusaLink API and shows live status on a web dashboard.

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Create your config
cp config.example.yaml config.yaml

# 3. Edit config.yaml with the printer IPs and API keys

# 4. Run the server
python server.py

# 5. Open the dashboard
#    → http://localhost:5001
```

## What It Does

- Polls all 4 printers every 5 seconds via PrusaLink API
- Shows live status, temperatures, and print progress
- Detects when prints complete
- Logs job history
- Exposes a REST API the drone system will use later when we get there

## API Endpoints

| Endpoint | Description |
|---|---|
| `GET /api/printers` | Status of all printers |
| `GET /api/printers/<id>` | Status of one printer |
| `GET /api/events` | Pending events (consumed on read) |
| `GET /api/events/peek` | Pending events (not consumed) |
| `GET /api/history` | Completed job history |
| `GET /api/health` | Server health check |
