# Data migrations

One-shot scripts that change the shape or contents of the SQLite DBs
under `data/`. Each script is named `NNN_short_description.py` where
`NNN` is the migration ID.

## How the registry works

A `schema_version` table lives in `data/work_orders.db`. It records
which migrations have been applied. Every migration script:

1. Declares its `MIGRATION_ID` and `DESCRIPTION` as module constants.
2. Calls `runner.is_applied(MIGRATION_ID)` on entry — if `True`,
   prints "already applied" and exits 0.
3. Opens its own transaction, makes its writes, and calls
   `runner.record(MIGRATION_ID, DESCRIPTION, conn)` as the last write
   before `COMMIT`. Registry + data commit together (or roll back
   together).

The runner module is `app/shared/migrations/runner.py`.

## Writing a new migration

Copy the template and fill in the migration body:

```sh
cp scripts/migrations/_template.py.example scripts/migrations/NNN_short_description.py
```

In the new file, set `MIGRATION_ID` and `DESCRIPTION`, then replace
`_perform_changes(conn)` with the actual writes. Replace
`_describe_plan(conn)` with a read-only summary for the `--dry-run`
path.

Then test locally with `--dry-run` against a copy of the production DB
(never against your live DB without a backup).

## The `.applied-<date>` rename convention

Pre-runner scripts (anything before this README was written — only
migration 001 at the time of writing) used a manual rename convention
to mark themselves as applied: the file was renamed from
`001_xxx.py` to `001_xxx.py.applied-YYYYMMDD` on the Pi after
running. This was the only safety against accidental re-execution.

**New scripts do not need to be renamed.** The runner's
`is_applied(MIGRATION_ID)` check makes re-execution a clean no-op,
which is safer than a filename-based guard. Existing pre-runner
scripts keep their `.applied-<date>` suffix as a backup belt-and-
suspenders — the script `000_record_migration_001.py` writes the
registry entry for migration 001 so the registry reflects the truth.

## Querying the registry

```sh
sqlite3 data/work_orders.db "SELECT * FROM schema_version ORDER BY applied_at;"
```

## The runner is not an orchestrator

It does not discover scripts in this directory, does not run them in
sequence, and does not own the migration lifecycle. Manual application
of each script remains the operator's job — stop the service, run the
script with `--apply`, verify, start the service.
