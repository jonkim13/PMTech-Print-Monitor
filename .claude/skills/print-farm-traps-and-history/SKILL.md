---
name: print-farm-traps-and-history
description: Debugging playbook + settled battles for print-farm-monitor. Use whenever investigating ANY bug — stuck statuses, queue items not completing, wrong filament grams, duplicate print_jobs rows, WO status not rolling up, data loss, migration questions — and BEFORE re-diagnosing anything that smells like a known issue. Also use before writing or running a DB migration, or doing anything to files under data/.
---

# Print Farm Monitor — Traps & History

Every battle below is settled. Do not re-fight one; check its status,
verify with the given command, and build on the fix. The two costliest
(per Jonathan, 2026-07-10) were **filament-grams** and
**stuck-printing** — both rooted in PrusaLink API behavior; see
`prusalink-reference` for the API facts themselves.

When NOT to use: general architecture questions → `print-farm-architecture`.
Run/test commands → `print-farm-build-and-run`.

## Debugging playbook

- Server logs use greppable prefixes: `[EVENT]` (transition outcome),
  `[WORKORDER]` (queue/queue_job changes), `[PRODUCTION]` (production-DB
  writes), `[CANCEL]` (stop-pending), `[SNAPSHOT-FAILED]`, `[CLEANUP]`.
  On the Pi: `sudo journalctl -u print-farm-monitor --since today | grep -E "EVENT|WORKORDER|PRODUCTION|CANCEL"`.
- For any status-drift symptom, `docs/audit/stuck-printing-diagnosis.md`
  has a full ready-made diagnostic kit: 8 SQL queries (Q1–Q8) across
  work_orders/production/history DBs, a failure-mode decision tree
  (Modes A–D), and log-search guidance. Start there.
- The single most informative signal in a "queue stuck but print done"
  case: `print_jobs.status` in production_log.db. `completed` → queue-side
  matching failed; `started` → poller never observed the transition.
- Inspect DBs directly: `sqlite3 -header -column data/work_orders.db "..."`.
  Fine to read the local dev DBs; **back up before any write** (see below).

## Settled battles

### 1. WO status propagation bug (Phase 0) — FIXED
Symptom: queue_items/jobs/WOs stuck while the printer finished fine.
Root cause: completion/fail lookups matched `status='printing'` strictly,
but items could be in `uploading/uploaded/starting` when the event fired;
the handler silently no-opped. Fix: every lookup widened to
`ACTIVE_QUEUE_STATUSES` (`app/domains/queue/execution_lifecycle.py`,
`queue_handler._matches_active_queue_job`), plus id-linkage captured at
print start so completion only falls back to filename matching as a
last resort. Migration 002
swept the historical drift. Rule going forward: never write a
`status='printing'`-only predicate.
Verify: `grep -n "ACTIVE_QUEUE_STATUSES" app/domains/queue/execution_lifecycle.py`

### 2. Stuck `printing` after touchscreen cancel — FIXED (same family)
Cancelling on the printer's physical touchscreen bypasses the API, so no
`_stop_pending` flag exists; the poller sees `printing → idle` and
classifies it as completion — which is fine *if* the queue-side match
succeeds. Five parts got stuck when it didn't (Mode A of the diagnosis
doc). Same widening fix as #1. The diagnosis doc remains the template
for any recurrence.

### 3. Stop-race: API stop misclassified as completion — FIXED
`DELETE /api/v1/job` makes the printer go `printing → idle`, identical
to a normal completion. Fix: `farm_manager.mark_stop_pending(printer_id)`
is called on every API-driven stop; the poll loop checks the flag within
`STOP_PENDING_TTL_SEC = 120` (farm_manager.py:27) and classifies the
transition as `print_stopped` instead. The flag is persisted
(`data/server_state.json`) so a restart mid-stop doesn't lose it.
Verify: `grep -n "STOP_PENDING_TTL_SEC" farm_manager.py`

### 4. Filament grams always 0.0 — FIXED (biggest investigation)
Symptom: `print_jobs.filament_used_g = 0`, `filament_used_source='none'`
for nearly every job. Root cause: the code fetched `/api/v1/job` *after*
the print FINISHED, and Prusa returns a blank/absent `meta` payload then.
Fix (commit 4a4e804): parse slicer metadata from the gcode **at upload
time** (`app/shared/gcode_metadata.py` — in-tree parser; the
`gcode-metadata` PyPI package does not exist), persist 12 `parsed_*`
columns on `upload_sessions`, copy onto `print_jobs` at print start.
`filament_used_source` resolution order: `parsed` > `api` > `filename`
(a `…_9.58g_…` token) > `mm_estimate` > `none`. Full trace:
`docs/investigations/filament-grams.md`.
Verify: `grep -n "parsed" app/domains/monitoring/production_materials.py | head`

### 5. FAT32 8.3-filename duplicate print_jobs — FIXED
Prusa firmware sometimes reports the same print under the DOS 8.3
truncated name (`LONGN~1.GCO`) in one code path and the long name in
another → two `print_jobs` rows ~79s apart. Fix: state-based dedup in
`app/domains/production/job_repository.py` (Phase 6, "FAT32 long↔short"
comment near line 134) + migration 004 reconciled historical orphans.

### 6. May 2026 live-DB overwrite incident — the reason for the snapshot layer
`git checkout HEAD -- data/*.db` overwrote three live DBs with stale
tracked copies; partial data loss went unnoticed for a week. Fixes: all
`.db` files are now untracked/gitignored (`git ls-files | grep '\.db$'`
must stay empty), and `app/shared/snapshots/runner.py` archives every DB
to `data/recovery/<timestamp>/` on each startup and before migrations
(WAL-safe via `sqlite3 .backup`, never raw file copy). Individual
migrations additionally write `<db>.bak-<timestamp>` beside each DB.
**Never git-checkout/restore anything into `data/`. Never flat-copy a
WAL-mode DB.**

### 7. The External-fold false premise — REJECTED, don't retry
2026-06-04: an attempt to "unify" External jobs into Internal was
discarded — the three job types encode HOW a part is made and are
correct as built. See the job-types section of `print-farm-architecture`.

### 8. `delivered` must survive resync — PINNED BY TEST
Any queue write used to be able to re-derive a delivered WO back to
`completed`. Guard: `sync_work_order_status` early-returns on
`delivered` (tests/test_delivered_status_survives_resync.py).

## Migrations — the two-track trap (most-bitten item)

There are TWO parallel schema-change mechanisms; contributors expect one:

1. **Registered one-shot scripts** — `scripts/migrations/NNN_*.py`
   (000–008 exist as of 2026-07-10). Recorded in the `schema_version`
   table in `work_orders.db` via `app/shared/migrations/runner.py`;
   re-running is a no-op. The runner is NOT an orchestrator — each
   script is run manually (stop service → `--dry-run` → `--apply` →
   verify → start). Full rules: `scripts/migrations/README.md`
   (authoritative — read it before writing one; template at
   `_template.py.example`).
2. **Inline per-repo migrations** — `add_column_if_missing` /
   `CREATE TABLE IF NOT EXISTS` calls inside each repository's
   `_init_db`, run automatically on boot, **not registered anywhere**.

So "what's the schema?" is answered by repo `_init_db` code, not by the
migration scripts alone. When adding a column: small/additive → inline
per-repo pattern; data-transforming or destructive → numbered script.
Never run a write migration against a live DB without a fresh backup.
Query the registry: `sqlite3 data/work_orders.db "SELECT * FROM schema_version ORDER BY applied_at;"`

## Test-update rule (Jonathan's standing feedback)

When a new phase legitimately changes behavior, update the prior tests
faithfully — don't delete pinned tests; invert them into regression
tests for the new behavior.

## Provenance

Verified 2026-07-10 against main @ 1b44b7f. Re-verify:

```sh
grep -n "STOP_PENDING_TTL_SEC" farm_manager.py
grep -n "_matches_active_queue_job" app/domains/monitoring/queue_handler.py
grep -rn -i "fat32" app/domains/production/job_repository.py | head -3
ls scripts/migrations/
git ls-files | grep '\.db$'        # must print nothing
```
