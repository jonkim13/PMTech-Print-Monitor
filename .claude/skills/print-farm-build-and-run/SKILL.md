---
name: print-farm-build-and-run
description: How to actually run, test, configure, and deploy print-farm-monitor — including the interpreter gotcha (repo venv has no pytest), the safe dev server, config.yaml/.env axes, the data/ directory map, Pi deploy reality, and the definition of done. Use whenever running the app or tests, verifying a change, editing config.yaml or .env, touching anything under data/ or deploy/, or preparing work for Jonathan to commit.
---

# Print Farm Monitor — Build, Run, Test, Deploy

When NOT to use: understanding the code's design → `print-farm-architecture`.
Diagnosing a bug or writing a migration → `print-farm-traps-and-history`.

## Interpreter reality (Mac dev box) — read first

- Use **system `python3`** (anaconda, `/opt/anaconda3/bin/python3`): it
  has flask + all deps + pytest 9.
- The checked-in `./venv` is a stale artifact: it has flask but **no
  pytest**. `venv/bin/python -m pytest` fails with "No module named
  pytest". Don't use it; don't "fix" it unless asked.
- README's quick start says `cp config.example.yaml config.yaml` —
  **`config.example.yaml` does not exist**. A real `config.yaml` and
  `.env` are already present in this working copy.

## Tests — the primary verification loop

```sh
python3 -m pytest tests/ -q        # 376 passed in ~5s (2026-07-10)
```

- Tests are self-contained (temp DBs / fixtures); they never touch
  `data/`. No printers or network needed.
- Run the full suite — it's 5 seconds. Single file:
  `python3 -m pytest tests/test_queue_service.py -v`.
- JS has no test suite; syntax-check edited files:
  `node --check static/js/pages/workorders/create.js`.
- Quick import check after Python edits:
  `python3 -m py_compile farm_manager.py` (or any file).
- Definition of done: full suite green, tree commit-ready, **no
  commits** (Jonathan commits manually). When a phase legitimately
  changes behavior, update prior tests rather than deleting them (see
  test-update rule in `print-farm-traps-and-history`).

## Running the app locally

Safe dev server (no printer polling — works with zero hardware):
`.claude/launch.json` defines `print-farm-web`, which runs
`create_app(start_poller=False)` on **port 5050**. Use the browser-pane
preview tools to start it. This is the right way to verify UI changes.

Full app (`python3 server.py`, **port 5001** from config.yaml) starts
the polling daemon, which will try to reach the four printers at their
`.local` hostnames every 5s — on the dev Mac that just logs connection
errors. Prefer the dev server unless you specifically need the poller.
Note: *any* app boot (dev server included) builds the container, which
snapshots every DB into `data/recovery/<timestamp>/` and runs the
gcode-uploads cleanup — harmless, but explains new recovery folders.

## Configuration

`config.yaml` (repo root, committed) — all axes:

| Key | Default | Notes |
|---|---|---|
| `poll_interval_sec` | 5 | printer poll cadence; browser poll derives from it (floor 1s) |
| `server_port` | 5001 | full-app port |
| `printers.<id>` | 4 printers | `name`, `model` (`core_one`\|`xl` — `xl` ⇒ 5 tools), `host` (mDNS `.local`), `username`, `password` |
| `db_path` | `data/FilamentInventory.db` | inventory DB only; all other DB paths are hardcoded in `app/config/settings.py` |
| `drone.*` | `enabled: false` | placeholder feature, leave off |

`.env` (repo root, gitignored) — printer passwords referenced from
config.yaml as `${VAR}`: `CORE_ONE_1_PASSWORD`, `CORE_ONE_2_PASSWORD`,
`XL_1_PASSWORD`, `XL_2_PASSWORD`. `load_settings()` substitutes them;
an unset var is left as the literal `${...}` string (no error).

## data/ directory map

| Path | What | Safe to touch? |
|---|---|---|
| `*.db` | live SQLite (see DB table in `print-farm-architecture`) | read yes; **write only with backup** |
| `server_state.json` | poller's previous-status + stop-pending markers across restarts | no |
| `recovery/<timestamp>/` | whole-fleet startup/pre-migration snapshots | read-only archive; app never reads it |
| `snapshots/` | print-completion camera PNGs | fine |
| `gcode_uploads/` | staged uploads, auto-deleted after 24h | fine |

All DBs are WAL-mode. Never flat-copy or git-restore a DB — see the
May 2026 incident in `print-farm-traps-and-history`.

## Deploy reality (Raspberry Pi)

Confirmed by Jonathan 2026-07-10: deploy = SSH to the Pi, `git pull`,
restart the service. Pi runs latest main. The service is systemd unit
`print-farm-monitor` running `venv/bin/python3 server.py` (the *Pi's*
venv, created by `deploy/install.sh` — unrelated to the stale Mac venv).

- `deploy/setup_service.sh` — one-time: writes the unit file, enables + starts.
- `deploy/restart_service.sh` / `stop` / `start` / `status` — `sudo systemctl ...` wrappers.
- `deploy/view_logs.sh` — `journalctl -u print-farm-monitor -f`.
- The unit loads `.env` via `EnvironmentFile` — the Pi needs its own `.env`.
- Pi project path is `/srv/print-farm-monitor` per docs/audit (unverified from this Mac).
- Registered migrations are NOT run automatically on deploy — apply
  manually per `scripts/migrations/README.md`.

## Document landscape — trust in this order

1. **Code + tests** — ground truth.
2. `docs/` (gitignored, Mac-local): `docs/audit/*` (per-domain audits,
   corrections-swept 2026-05-22), `docs/audit/stuck-printing-diagnosis.md`,
   `docs/investigations/filament-grams.md`, `scripts/migrations/README.md`
   (committed, authoritative for migrations). Good but can drift.
3. `README.md` — committed but stale (describes the original
   monitoring-only scope; wrong quick start).
4. Root `PROJECT_MEMORY.md`, `CODEBASE_AUDIT.md`, `REFACTOR_PLAN.md`,
   `WORKORDER_AUDIT.md` — **historical**, describe the pre-refactor flat
   layout. Do not use as current reference.

## Provenance

Verified 2026-07-10 against main @ 1b44b7f (tests actually run; deploy
scripts read; config/env read). Re-verify:

```sh
python3 -m pytest tests/ -q                          # suite size & green
cat .claude/launch.json                              # dev-server config
grep -n "server_port\|poll_interval" config.yaml
grep -c "PASSWORD" .env                              # 4 printer secrets
ls data/
```
