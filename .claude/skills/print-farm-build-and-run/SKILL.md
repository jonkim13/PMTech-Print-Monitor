---
name: print-farm-build-and-run
description: How to actually run, test, configure, and deploy print-farm-monitor — including the interpreter gotcha (use ./venv, never anaconda python3 — its cv2 is broken), the safe dev server, config.yaml/.env axes, the data/ directory map, Pi deploy reality, and the definition of done. Use whenever running the app or tests, verifying a change, editing config.yaml or .env, touching anything under data/ or deploy/, or preparing work for Jonathan to commit.
---

# Print Farm Monitor — Build, Run, Test, Deploy

When NOT to use: understanding the code's design → `print-farm-architecture`.
Diagnosing a bug or writing a migration → `print-farm-traps-and-history`.

## Interpreter reality (Mac dev box) — read first

- Use the checked-in **`./venv`** (Python 3.12.3). It is the canonical
  interpreter for both the test suite and the dev server, and it has
  everything: pytest 9.1.1, flask, and the engraving stack (cv2 5.0.0,
  numpy 2.5.1, matplotlib, trimesh, pillow, fast-simplification).
- **Do not use anaconda's `python3`** (`/opt/anaconda3/bin/python3`,
  Python 3.11). Its cv2 is broken — importing it raises
  `AttributeError: module 'cv2.dnn' has no attribute 'DictValue'` — so
  every engraving test errors out under it. It is the shell's default
  `python3`, so reach for `./venv/bin/python` explicitly.
- README's quick start says `cp config.example.yaml config.yaml` —
  **`config.example.yaml` does not exist**. A real `config.yaml` and
  `.env` are already present in this working copy.

## Tests — the primary verification loop

```sh
./venv/bin/python -m pytest tests/ -q   # 414 passed in ~35s (2026-07-23)
```

- Tests are self-contained (temp DBs / fixtures); they never touch
  `data/`. No printers or network needed.
- Run the full suite — it's ~35s (the engraving generation tests are
  ~5s each). Single file:
  `./venv/bin/python -m pytest tests/test_queue_service.py -v`.
- JS has no test suite; syntax-check edited files:
  `node --check static/js/pages/workorders/create.js`.
- Quick import check after Python edits:
  `./venv/bin/python -m py_compile farm_manager.py` (or any file).
- Definition of done: full suite green, tree commit-ready, **no
  commits** (Jonathan commits manually). When a phase legitimately
  changes behavior, update prior tests rather than deleting them (see
  test-update rule in `print-farm-traps-and-history`).

## Running the app locally

Safe dev server (no printer polling — works with zero hardware):
`.claude/launch.json` defines `print-farm-web`, which runs
`create_app(start_poller=False)` on **port 5050** via
`./venv/bin/python` (the same canonical interpreter as the tests — the
app needs working cv2 at request time for engraving). Use the
browser-pane preview tools to start it. This is the right way to verify
UI changes.

Full app (`./venv/bin/python server.py`, **port 5001** from config.yaml) starts
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
own venv, created by `deploy/install.sh` — a separate thing from the Mac
`./venv`, though both are built from `requirements.txt`).

- `deploy/setup_service.sh` — one-time: writes the unit file, enables + starts.
- `deploy/restart_service.sh` / `stop` / `start` / `status` — `sudo systemctl ...` wrappers.
- `deploy/view_logs.sh` — `journalctl -u print-farm-monitor -f`.
- The unit loads `.env` via `EnvironmentFile` — the Pi needs its own `.env`.
- Pi project path is `/srv/print-farm-monitor` per docs/audit (unverified from this Mac).
- Registered migrations are NOT run automatically on deploy — apply
  manually per `scripts/migrations/README.md`.

## Document landscape — trust in this order

1. **Code + tests** — ground truth.
2. Session summaries, RESULTS files, and diagnostic writeups belong in
   `docs/notes/` (**committed**) — write them there, not at the repo root. It
   travels with the repo, so it is readable from the Pi or any clone.
3. `docs/` otherwise (gitignored, Mac-local): `docs/audit/*` (per-domain audits,
   corrections-swept 2026-05-22), `docs/audit/stuck-printing-diagnosis.md`,
   `docs/investigations/filament-grams.md`, `scripts/migrations/README.md`
   (committed, authoritative for migrations). Good but can drift.
4. `README.md` — committed but stale (describes the original
   monitoring-only scope; wrong quick start).
5. Root `PROJECT_MEMORY.md`, `CODEBASE_AUDIT.md`, `REFACTOR_PLAN.md`,
   `WORKORDER_AUDIT.md` — **historical**, describe the pre-refactor flat
   layout. Do not use as current reference.

## Provenance

Verified 2026-07-10 against main @ 1b44b7f (tests actually run; deploy
scripts read; config/env read). Interpreter section re-verified
2026-07-23 against main @ 27b928d — the previous "./venv has no pytest,
use anaconda python3" guidance was inverted and is now corrected.
Re-verify:

```sh
./venv/bin/python -V                                 # 3.12.x
./venv/bin/python -m pytest tests/ -q                # suite size & green
./venv/bin/python -c "import cv2, trimesh, pytest"   # engraving stack present
python3 -c "import cv2"                              # anaconda: expect DictValue AttributeError
cat .claude/launch.json                              # dev-server config -> ./venv/bin/python
grep -n "server_port\|poll_interval" config.yaml
grep -c "PASSWORD" .env                              # 4 printer secrets
ls data/
```
