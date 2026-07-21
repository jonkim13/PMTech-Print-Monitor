---
name: print-farm-architecture
description: Load-bearing design of the print-farm-monitor app. Use whenever modifying anything under app/ (especially work_orders, queue, monitoring, quality, triage), changing status derivation or the jobs/queue_items data model, adding a domain/route/service, touching the DI container, adding a DB table or column, or deciding where new code should live. Also use before proposing any restructuring — several "weird" decisions here are deliberate.
---

# Print Farm Monitor — Architecture

Single-process Flask + SQLite operations console for a 4-printer Prusa
farm (2 Core One, 2 XL). Polls each printer's PrusaLink HTTP API every
5s, drives uploads/print-starts, tracks filament inventory, persists
production records for ISO-9001-style traceability, and manages customer
work orders. Built in numbered phases (0–G complete as of 2026-06-22).

When NOT to use this skill: running/testing/deploying → `print-farm-build-and-run`.
Debugging a misbehavior → `print-farm-traps-and-history` first.
PrusaLink API behavior → `prusalink-reference`.

## Process shape

```
server.py                     thin compatibility shim (python3 server.py)
  └─ app/main.py              create_app() factory + main()
       ├─ app/config/settings.py    loads .env + config.yaml → AppSettings (frozen dataclass, all paths)
       ├─ app/config/container.py   build_container() → AppContainer: EXPLICIT DI, built once per process
       └─ registers one blueprint per domain (below)
farm_manager.py (repo root)   PrintFarmManager: ONE daemon polling thread over all printers
prusalink.py  (repo root)     PrusaLinkClient: HTTP client, one instance per printer
```

- The container is built linearly: repos → runtime infrastructure →
  `TransitionHandler` → `farm_manager` → services. All injection is via
  constructor kwargs (Phase 5h). **Never late-bind** (`service.x = y`
  after construction) — that pattern was deliberately removed.
- `farm_manager.py` and `prusalink.py` intentionally still live at repo
  root, not in `app/` — they predate the package and everything imports
  them from there.
- The poll loop classifies status transitions
  (`app/domains/monitoring/transition_detector.py`) and dispatches to
  `TransitionHandler`, which fans out to `ProductionHandler`,
  `QueueHandler`, `FilamentHandler` (all under `app/domains/monitoring/`).

## Domains (app/domains/)

| Domain | Owns |
|---|---|
| `work_orders` | WO CRUD, jobs, deliveries, **status_sync.py (canonical status derivation)** |
| `queue` | queue_items/queue_jobs repos, execution lifecycle, bulk ops |
| `execution` | upload → verify → start workflow (upload_sessions.db) |
| `monitoring` | transition detection/handling, events, runtime state |
| `production` | print_jobs, machine_log, material_usage, CSV export |
| `quality` | NCRs (non-conformance reports), quality.db |
| `triage` | aggregated 5-lane `/api/triage` payload (read-only composition) |
| `inventory`, `assignments` | filament spools; per-printer/per-tool spool mapping |
| `printers`, `dashboard`, `reports`, `drone` | status API, dashboard JSON, weekly CSV log, drone placeholder (mock only) |

Each domain: `repository.py` (SQL) → `service.py` (logic) → `routes.py`
(Flask blueprint). Routes never touch SQL directly.

## Data model (the part that bites)

Five+ SQLite files under `data/`, one per concern. **Invariant: no SQL
joins across DB files — cross-DB aggregation happens in Python**
(see `TriageService`, `DashboardService`).

| DB file | Tables |
|---|---|
| `work_orders.db` | `work_orders`, `jobs`, `line_items`, `queue_items`, `queue_jobs`, `deliveries`, `schema_version` |
| `production_log.db` | `print_jobs`, `machine_log`, `material_usage` |
| `quality.db` | NCR tables (migration 007) |
| `print_history.db` | poller transition events |
| `FilamentInventory.db` | spools |
| `assignments.db` | (printer_id, tool_index) → spool |
| `upload_sessions.db` | upload sessions incl. 12 `parsed_*` gcode-metadata columns |

Hierarchy: `work_orders` → `jobs` (typed) → `line_items` → `queue_items`
(one row per physical part). `queue_jobs` is the *execution* record for
one upload/print attempt (a queue_job groups the queue_items sent to a
printer in one start). `print_jobs` (production DB) is the traceability
record, linked by id at start time — **not** by filename matching (that
was the Phase 0 family of bugs; see traps skill).

### The three job types are AUTHORITATIVE — do not unify them

The axis is HOW the part is made; all three are for the customer. A
2026-06-04 attempt to fold External into Internal was based on a false
premise and discarded.

- **Internal** — printed in-house. Has line_items/queue_items. Flow: print → inspect → deliver.
- **External** — vendor-made part. Vendor + Process fields, **no queue_items**. Flow: arrives → incoming inspection → deliver.
- **Design** — design service. Designer + Requirements, no parts, **skips the inspection gate**.

## Status derivation — single source of truth

`app/domains/work_orders/status_sync.py` owns ALL rollup rules. Every
repository calls through it. Vocabulary:

- queue_items: `queued / uploading / uploaded / starting / printing / completed / failed / upload_failed / start_failed / cancelled` (constants in `app/shared/constants.py` — the strings are stored in DBs; renaming is a data migration)
- jobs & work_orders: `open / in_progress / completed / cancelled / attention`; work_orders additionally `delivered`

Layered derivation — respect the layers:

1. `derive_job_status` / `derive_work_order_status` — base queue-only
   rollups. **Must stay untouched**; new behavior goes in the
   `_combined` siblings.
2. `derive_job_status_combined` — Phase D inspection gate. When queue
   rollup says `completed`: `inspection_outcome` pass → `completed`,
   fail → `attention`, pending → `in_progress`. Design jobs skip the
   gate. The gate signal lives ONLY on the job row (`jobs.inspection_outcome`),
   never on queue_items — so an Internal job's status can legitimately
   diverge from its queue_items rollup.
3. `derive_work_order_status_combined` — pools queue_items + ALL job
   statuses (projected into queue vocabulary). Phase E: an open NCR
   gates `completed` → `attention`.
4. `delivered` (Phase F) is a manual terminal status set only via
   `set_work_order_status_terminal` — never derived, and
   `sync_work_order_status` early-returns on it so nothing can re-derive
   a delivered WO. Never write WO status with ad-hoc UPDATEs.

Predicate rule learned from Phase 0: any lookup that matches "the thing
currently printing" must accept ALL of `ACTIVE_QUEUE_STATUSES`
(`uploading/uploaded/starting/printing`), never `status='printing'`
alone.

## Frontend

No framework, no bundler. `templates/base.html` + partials
(`templates/partials/pages/`, `templates/partials/modals/` — 19 modal
partials as of 2026-07-10), one extra top-level page
`templates/wo_detail.html`. JS in
`static/js/core/` (api, dom, nav, state, status) and
`static/js/pages/<page>/`, loaded in fixed `<script>` order — load
order matters. Two distinct onclick disciplines coexist (bare globals
vs namespace globals, documented in `docs/audit/06-frontend.md`); don't
collapse them into one.

## Deliberate decisions that look wrong

- **No auth, wide-open CORS, Flask built-in server in prod** — internal-LAN
  tool on a Pi; accepted.
- **Multiple SQLite files** — per-domain isolation + independent
  snapshot/restore; not an accident.
- **Polling, no websockets** — browser polls `/api/printers`; Python
  daemon polls printers. Simple and sufficient.
- **~200-line file target** (Jonathan's rule, stated 2026-07-10): files
  are split aggressively to stay small. It's aspirational — e.g.
  `work_orders/service.py` (984), `work_orders/routes.py` (812),
  `prusalink.py` (907) exceed it. Prefer splitting new code; don't
  bulk-refactor the violators without being asked.
- **Agents never commit** — Jonathan commits manually; leave a
  commit-ready tree.

## Known weak points (2026-07-10)

- **Two-track migrations** — the most-bitten item; see traps skill.
- Defensive lazy-builds in `farm_manager.py` (`getattr(self, ..., None)`
  fallbacks) are dead code since Phase 5h; cleanup candidate, don't rely on them.
- `_attach_queue_job_metadata` runs an extra GROUP BY per single-row
  queue read (perf smell, audit #18).
- `drone.py` + drone domain are a mock placeholder; `config.yaml`
  `drone.enabled: false`.
- Jonathan is (2026-07-10) unsure the domain-split structure is right
  long-term — structural feedback is welcome, but as a proposal, not a
  drive-by refactor.

## Provenance

Verified 2026-07-10 against main @ 1b44b7f. Re-verify volatile facts:

```sh
grep -n "ACTIVE_QUEUE_STATUSES" app/domains/work_orders/status_sync.py   # status pools
grep -rn "def derive_" app/domains/work_orders/status_sync.py            # deriver set
ls app/domains/                                                          # domain list
grep -n "delivered" app/domains/work_orders/status_sync.py               # terminal guard
wc -l app/domains/work_orders/service.py                                 # 200-line-rule violators
```
