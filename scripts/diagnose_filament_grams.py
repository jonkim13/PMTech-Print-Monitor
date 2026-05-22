"""Read-only diagnostic: capture raw /api/v1/job responses per printer.

Stage B for the filament-grams investigation
(docs/investigations/filament-grams.md). Run **during an active print** on
each printer model so we can see what PrusaLink actually returns in
`data["file"]["meta"]`.

Behavior contract:
- Builds AppSettings via load_settings() (same path as container.py).
- Iterates settings.config["printers"] and constructs a PrusaLinkClient
  with the same kwargs farm_manager.py uses (printer_id, name, host,
  username, password, model, upload_storage).
- For each printer, prints:
    1. The redacted printer config (password masked).
    2. The HTTP status of GET /api/v1/job.
    3. The raw JSON response body.
    4. The normalized dict from client.get_job_details() — for
       comparison with the raw body.
- Does NOT write to any DB, file, or printer endpoint.
- Runs once and exits with status 0 even if individual printers error.

Usage on the Pi (during an active print):
    python scripts/diagnose_filament_grams.py
"""

import json
import os
import sys

# Make the project root importable when invoked from anywhere.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.config.settings import load_settings  # noqa: E402
from prusalink import PrusaLinkClient  # noqa: E402


def _redact_config(pcfg):
    """Return a shallow copy of the printer config with password masked."""
    redacted = dict(pcfg or {})
    if "password" in redacted and redacted["password"]:
        redacted["password"] = "***REDACTED***"
    return redacted


def _build_client(printer_id, pcfg):
    """Mirror farm_manager.py:85-93 — same PrusaLinkClient construction."""
    return PrusaLinkClient(
        printer_id=printer_id,
        name=pcfg["name"],
        host=pcfg["host"],
        username=pcfg.get("username", "maker"),
        password=pcfg.get("password", ""),
        model=pcfg.get("model", "unknown"),
        upload_storage=pcfg.get("upload_storage", "usb"),
    )


def _print_section(title):
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def _dump_raw_job(client):
    """Hit /api/v1/job and print the raw response (status + JSON body).

    Uses _request_raw to avoid the raise_for_status path; we want to see
    the body even on 4xx/5xx. Read-only.
    """
    try:
        resp = client._request_raw("/api/v1/job")
    except Exception as exc:
        print("  HTTP error: {} ({})".format(
            exc.__class__.__name__, exc
        ))
        return

    print("  HTTP status: {}".format(resp.status_code))
    print("  Content-Type: {}".format(resp.headers.get("Content-Type", "")))
    body_text = resp.text or ""
    if not body_text:
        print("  Body: <empty>")
        return

    try:
        body_json = resp.json()
        print("  Body (parsed JSON):")
        print(_indent(json.dumps(body_json, indent=2, sort_keys=True), 4))
    except ValueError:
        print("  Body (raw text, not JSON):")
        print(_indent(body_text, 4))


def _dump_normalized_details(client):
    """Print what client.get_job_details() returns — the dict our code uses."""
    details = client.get_job_details()
    print("  Normalized details (what get_job_details returns):")
    print(_indent(json.dumps(details, indent=2, sort_keys=True, default=str), 4))


def _indent(text, spaces):
    pad = " " * spaces
    return "\n".join(pad + line for line in text.splitlines())


def main():
    settings = load_settings()
    printers = (settings.config or {}).get("printers", {}) or {}
    if not printers:
        print("No printers configured in settings.config['printers'].")
        return 0

    print("Diagnosing {} printer(s). Run during an active print for best "
          "signal.".format(len(printers)))

    for printer_id, pcfg in printers.items():
        _print_section("printer_id={}".format(printer_id))
        print("  Config (redacted): {}".format(
            json.dumps(_redact_config(pcfg), sort_keys=True)
        ))
        try:
            client = _build_client(printer_id, pcfg)
        except Exception as exc:
            print("  Client construction failed: {} ({})".format(
                exc.__class__.__name__, exc
            ))
            continue

        print()
        print("  --- GET /api/v1/job (raw) ---")
        _dump_raw_job(client)

        print()
        print("  --- get_job_details() (normalized) ---")
        _dump_normalized_details(client)

    print()
    print("Done. Paste the output above into the investigation thread.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
