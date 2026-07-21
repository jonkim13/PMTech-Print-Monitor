---
name: prusalink-reference
description: How the PrusaLink printer HTTP API *actually* behaves, as battle-tested on this farm's Core One and XL printers — blank job metadata after completion, FAT32 8.3 filenames, stop/cancel status ambiguity, auth fallback, per-tool XL data, bgcode parsing limits. Use whenever touching prusalink.py, farm_manager.py polling/classification, upload/verify/start flow, filament deduction, or debugging anything where printer-reported data looks wrong or missing.
---

# PrusaLink API — As It Behaves Here

The two costliest bugs in this project (filament-grams, stuck-printing)
were both caused by trusting PrusaLink to behave reasonably. It doesn't
always. This is the observed behavior on this farm's firmware (2 Core
One, 2 XL with 5 tools each), not the official docs.

When NOT to use: the fixes' full war stories → `print-farm-traps-and-history`.
App architecture → `print-farm-architecture`.

## Client basics (`prusalink.py`, repo root)

One `PrusaLinkClient` per printer, hosts are mDNS `.local` names,
username `maker`, passwords from `.env`. Auth is **Digest first, flip to
Basic on 401** — some firmware versions only speak Basic; the client
remembers the flip (`use_basic`) for the rest of the process.

Endpoints in use:

| Call | Endpoint | Used for |
|---|---|---|
| `poll()` | `GET /api/v1/status` | state, temps, progress, current filename — the 5s poll |
| `get_job_details()` | `GET /api/v1/job` | job/file metadata incl. filament fields |
| `get_files()` | `GET /api/v1/storage[/{name}]` | printer file listing |
| `upload_file()` | `PUT /api/v1/files/{storage}/{path}` | upload with `Overwrite: ?1`, retries, no print start |
| `start_file_print()` | `POST /api/v1/files/{storage}/{path}` | start an already-uploaded file |
| `stop_job()` | `DELETE /api/v1/job` | stop current print |
| `get_camera_snapshot()` | `GET /api/v1/cameras/snap` | completion PNG |
| transfer check | `GET /api/v1/transfer` | is an upload still in flight (verify step) |

Upload → verify → start are **separate steps** (ExecutionService,
`app/domains/execution/service.py`). `upload_gcode(print_after=True)` is
a backward-compat wrapper only; don't build new flows on it.

## The quirks (each one cost real time)

### 1. `/api/v1/job` metadata goes blank after the print finishes
While printing, `file.meta` carries `filament used [g]`, per-tool
arrays, layer height, etc. Once the printer reaches FINISHED, the same
endpoint returns a blank/absent `meta`. **Never fetch filament usage at
completion time.** The app parses gcode at upload
(`app/shared/gcode_metadata.py`) and copies values onto `print_jobs` at
start; resolution order `parsed > api > filename > mm_estimate > none`.

### 2. FAT32 8.3 filenames
The USB storage is FAT32; some firmware code paths report a running
print under the DOS short name (`LONGN~1.GCO`), others under the long
name — for the *same* print. Anything matching printer-reported
filenames must tolerate both (dedup lives in
`app/domains/production/job_repository.py`). Completion routing
(`queue_handler.complete`) therefore tries id-linkage captured at print
start first, then printer-scoped lookup, and only falls back to
filename matching last — with `normalize_print_filename`
(`runtime_state.py`: basename+strip+lower) as the comparator. Keep that
ordering.

### 3. Stop and cancel are invisible in the status stream
- API stop (`DELETE /api/v1/job`): the printer just goes
  `printing → idle` — byte-identical to a successful completion. The app
  disambiguates with `mark_stop_pending()` + a 120s TTL window
  (farm_manager.py).
- Touchscreen cancel: no API involvement at all, same
  `printing → idle`, and the firmware may keep reporting `printing`
  until the user clears the confirmation screen (so the transition can
  also arrive *late*).
- Consequence: a `printing → idle/finished` transition alone can mean
  completed, stopped, or cancelled. Base classification (complete/
  started/error) lives in `app/domains/monitoring/transition_detector.py`;
  the stop-pending override sits in the `farm_manager.py` poll loop,
  which routes to `TransitionHandler.handle_print_stopped` instead.

### 4. XL per-tool filament data
XL (`model: xl` ⇒ 5 tools, `PrinterService.get_tool_count`) reports
per-tool arrays in job meta: `filament used [g] per tool`,
`filament used [mm] per tool`. Deduction prefers per-tool values against
per-tool spool assignments and falls back to tool-0 single values;
mm-only data estimates grams via `MM_TO_GRAMS_FACTOR = 0.00298`
(`filament_usage.py`, repo root — also home of the
`filament_used_source` constants).

### 5. `.bgcode` is only partially parseable
The in-tree parser handles `.gcode` fully and *uncompressed* `.bgcode`
SlicerMetadata blocks plus a best-effort ASCII scan. Compressed blocks
(heatshrink/zstd/deflate) land in `parse_error` — expected, not a bug;
the source chain falls back to `filename`/`none`. (The `gcode-metadata`
PyPI package does not exist — don't add it to requirements.) Accepted
upload extensions are `_ALLOWED_UPLOAD_EXTENSIONS` in
`app/domains/queue/service.py`: `.gcode .gco .g .bgcode`.

### 6. `/api/v1/status` never carries filament fields
`status_mapper.py` maps only state/temps/progress/display-filename. If
you need job metadata, it must come from `/api/v1/job` *during* the
print, or from the parsed upload session. (This dead-end hypothesis was
chased once — see `docs/investigations/filament-grams.md`.)

### 7. Upload timing
Large uploads over Wi-Fi are slow and can still be mid-transfer when the
route returns; the verify step polls `/api/v1/transfer` and the storage
listing before start. Accepted file extensions:
`.gcode .gco .g .bgcode`.

## Provenance

Verified 2026-07-10 against main @ 1b44b7f (prusalink.py read; behaviors
per docs/investigations/filament-grams.md and
docs/audit/stuck-printing-diagnosis.md; firmware behavior itself is
live-observed, may change with firmware updates). Re-verify:

```sh
grep -n "api/v1" prusalink.py | head -20
grep -n "use_basic" prusalink.py | head -5
grep -n "MM_TO_GRAMS_FACTOR" filament_usage.py
grep -rn "per tool" prusalink.py | head -5
```
