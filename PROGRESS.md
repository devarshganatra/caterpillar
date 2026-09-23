# CAT Co-Pilot: Project Progress & Handoff

Welcome to the CAT Co-Pilot project! This document outlines the current state of the architecture, what has been completed, and exactly how to spin up the local development environment.

## 🚀 Current State of the System

We have successfully built a full-stack, event-driven IoT system with robust Role-Based Access Control (RBAC), a deterministic cryptographic audit chain, and a live React dashboard.

### 1. Backend (FastAPI + TimescaleDB + Redis)
- **Ingestion & Sharding**: The `/ingest/telemetry` endpoint accepts cryptographically signed HMAC payloads from the simulator, verifies them, and deterministically shards them (using `SHA-256(machine_id) % 4`).
- **Atomic State Machine**: A Lua script runs natively in Redis to process telemetry, guaranteeing idempotency, sequence validation, and gap detection.
- **Worker & Persistence**: A background worker consumes Redis Streams, aggregates data, and persists it to TimescaleDB (`MachineStateLog`, `Event`, `Alert`).
- **Cryptographic Audit Log**: A highly secure `audit_log` table tracks all actions. Each row computes a `current_hash` based on the `prev_hash + payload_hash`, forming a tamper-proof chain.
- **Secure WebSockets**: A stateless `/auth/ws-ticket` endpoint issues 60-second JWTs to authenticate WebSocket connections in the `ws.py` gateway.

### 2. Frontend (React + Vite + Tailwind v4)
- **Authentication**: JWT-based `/login` with strict Role-Based Access Control (Operator, Supervisor, Admin).
- **Operator View (PreStart & HUD)**: Operators can fetch live tasks, complete safety checks, and transition to a real-time HUD driven by the Redis pub/sub WebSocket stream. The 3D vis is currently a lightweight hardware-accelerated 2.5D CSS module.
- **Supervisor Dashboard**: Supervisors get a multiplexed view of all machines on their authorized sites. Each `MachineCard` maintains its own secure WS stream.
- **Demo Director (Admin)**: A dedicated testing screen featuring a button to recalculate and verify the PostgreSQL cryptographic Audit Chain.

### 3. Simulation Environment
- The `simulator/sim.py` generates deterministic IoT data based on physical machine models defined in `contracts/machine_config.py`.
- It dynamically generates HMAC signatures and posts them to the local FastAPI backend.

---

## 🛠️ How to Get Started

Follow these exact steps to clone the repo, spin up the Docker containers, and run the system.

### Prerequisites
- Python 3.12+
- Node.js (v18+)
- Docker & Docker Compose

### Step 1: Clone the Repo
```bash
git clone <your-repo-url>
cd caterpillar
```

### Step 2: Start the Infrastructure
We use Docker exclusively for Redis and TimescaleDB (PostgreSQL).
```bash
# Spin up Redis and DB in detached mode
make up

# (Optional) View logs to ensure they started correctly
make logs
```

### Step 3: Setup the Python Backend
We run FastAPI natively to avoid long Docker rebuilds during rapid development.

```bash
# Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows use: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run the database migrations (Alembic)
alembic upgrade head

# Seed the database with Users, Machines, and Tasks
PYTHONPATH=. python seed_db.py

# Start the FastAPI server
python -m backend.app.main
```
*The backend will be available at `http://localhost:8000`*

### Step 4: Run the Simulation Worker
In a new terminal (don't forget to activate the venv and set `PYTHONPATH=.`):
```bash
source venv/bin/activate
PYTHONPATH=. python -m backend.app.worker.entrypoint
```

### Step 5: Start the IoT Simulator
In another terminal (activate venv):
```bash
source venv/bin/activate
PYTHONPATH=. python simulator/sim.py --scenario simulator/scenarios/demo.yaml
```

### Step 6: Start the React Frontend
In a new terminal:
```bash
cd web
npm install
npm run dev
```
*The frontend will be available at `http://localhost:5173`*

---

## 🔑 Demo Credentials

The database is pre-seeded with the following accounts for testing:

| Role | Username | Password | Notes |
| :--- | :--- | :--- | :--- |
| **Operator** | `operator` | `demo123` | Has access to SITE-A. Assigned to EXC001. |
| **Supervisor**| `supervisor`| `demo123` | Has access to SITE-A. Cannot see SITE-B machines. |
| **Admin** | `admin` | `demo123` | Has global access and can verify the audit chain. |

---

## 🧪 Testing

To run the integration tests (which verify deterministic signatures and WebSocket RBAC isolation boundaries):
```bash
source venv/bin/activate
PYTHONPATH=. pytest tests/ -v
```

Good luck! 🚀

---

## Stage 3 / Batch 3.0: Pre-flight fixes  (2026-09-24)

**Status:** ✅ verified

**Goal:** fix the hot-path contract gaps (identified by inspecting the repo against `BUILD_PLAN_new.md`/`ARCHITECTURE_new.md`) that would otherwise corrupt or block the warm/correlator/cold path work in Batches 3A–3J. See `PLAN_STAGE3_INTELLIGENCE.md` §1.1 for the full gap list (G1–G13). No warm/correlator/cold code was added in this batch — hot path only, plus test infra and repo hygiene.

**Changed files:**
- `contracts/ids.py` (new) — deterministic UUIDv5 ID helpers (`hot_event_id`, `warm_event_id`, `eta_slip_event_id`, `incident_id`, `window_id`) for all of Stage 3.
- `contracts/demo_assignments.py` (new) — maps demo machines to the seeded operator/task rows.
- `contracts/events.py` — added `EventType` enum (reference list) and `UiPushType.anomaly` (additive).
- `backend/app/worker/hot.py`:
  - **G2** `site_id` is now looked up from `contracts.machine_config.MACHINES`, not hardcoded `"SITE-A"`.
  - **G4** event IDs are now deterministic (`hot_event_id`), assigned before `arbitrate()`, so replaying a frame reproduces the same event identity.
  - **G3** `event_buffer` now stores `(frame_seq, event)` pairs; `flush_buffers` persists each event against its own `frame_seq` instead of the state's `last_seq` at flush time (previously events from different frames of the same type silently collided on the `uq_events_machine_seq_type` constraint and were dropped).
  - **G1** after each DB commit, buffered events are now `XADD`ed to `events:{shard}` (new stream) for the warm worker/correlator to consume in later batches.
  - **G5** the hot worker now reads `context:{site_id}` from Redis (throttled to 1×/wall-clock-second) and feeds it into `calculate_envelope`; a change in `active_conditions` triggers an `envelope` UiPush. Previously `last_context_snapshot` was never populated, so rain/mud never affected the safety envelope.
  - **G6** added `ui_mode` (`HUD` | `IDLE_HUB`) to `machine_state:{id}` and the `state_change` push, flipping to `IDLE_HUB` after `IDLE_HUB_DWELL_TICKS=30` consecutive IDLE frames (ARCH §6.1). The frontend wiring for this lands in Batch 3I.
- `backend/app/services/stream.py`:
  - Added `parse_flat_context()` (inverse of `flatten_context`).
  - **Bug found during manual verification (not in the original G1–G13 list):** `publish_context()` accessed `frame.sig`, but `ContextFrame` has no `sig` field — every `POST /ingest/context` call raised `AttributeError` and returned 500. Context had never actually reached Redis. Fixed by removing the dead `sig` write.
- `simulator/sim.py` (**G9**):
  - `create_initial_state` now uses `contracts.demo_assignments` for `operator_id`/`task_id` by default (matches the seeded `TASK-001`/`TASK-002` rows); `--random-ids` restores the old random-ID behaviour.
  - Added a deterministic hauler-queue Markov model (`truck_present`, `hauler_queue_len` now actually vary instead of being hardcoded `False`/`0`).
  - `apply_scenario_overrides` now supports `set: {mode: idle|travel|working}` (converts to `SimMode`) alongside the existing raw field overrides.
  - `--seed` now defaults to `42` (was a random seed) for reproducible demo runs.
- `simulator/scenarios/demo.yaml`: the `context:` block was missing `visibility_m`, `ambient_temp_c`, `wind_kmh`, `daylight` — required `ContextFrame` fields — so context ingest always 422'd. Filled in with the scenario's RAIN/MUDDY values.
- `.gitignore`: un-ignored `web/src/lib/` (it was being swallowed by the generic `lib/` Python-artifact rule — `api.ts`/`utils.ts` were never tracked); added `ml/data/` (regenerable, for future batches).
- `tests/conftest.py`: removed the custom `event_loop` fixture (incompatible with pytest-asyncio 1.x, which is what's installed).
- `pytest.ini` (new): `asyncio_mode = auto`, `unit`/`integration`/`e2e` markers.
- Fixed the wrong hot-worker run command in this file (was `backend.app.workers.hot_worker`, actually `backend.app.worker.entrypoint`).

**New tests:**
- `tests/unit/test_ids.py` (9) — determinism/uniqueness of every ID helper.
- `tests/unit/test_hot_worker.py` (8) — `process_frame` behaviour with mocked Redis/DB: site lookup, deterministic IDs across replay, per-event frame_seq, `ui_mode` transitions, context→envelope push, duplicate-seq skip.
- `tests/unit/test_simulator.py` (9) — deterministic assignment, hauler queue model bounds/determinism, scenario override handling incl. `mode`.
- `tests/unit/test_stream_context.py` (3) — regression test for the `publish_context` bug, `parse_flat_context` roundtrip.
- `tests/integration/test_events_stream.py` (3, needs `docker-compose up`) — two frames → two DB rows + two stream messages; replaying a message creates no duplicate; SITE-B machine events carry `site_id="SITE-B"`.
- `tests/integration/test_context_envelope.py` (1) — a RAIN/MUDDY context hash is picked up by `process_frame`.

**Tests executed (command + result):**
```
PYTHONPATH=. venv/bin/python -m pytest tests/ core/copilot_core/tests/ -q
43 passed in 2.35s
```
(29 pre-existing tests + 14 new; `docker-compose up` was running for the integration/batch4/batch5 tests.)

**Manual verification:**
1. `docker-compose up -d`, `alembic upgrade head`, `seed_db.py`.
2. Started `uvicorn backend.app.main:app` and `python -m backend.app.worker.entrypoint` on the current code.
3. Ran `simulator/sim.py --scenario simulator/scenarios/demo.yaml --machine EXC001 --seed 42` for ~120 simulated seconds (12s wall at `SIM_SPEED=10`) — zero HTTP errors.
4. `HGETALL context:SITE-A` → populated with RAIN/MUDDY/12mm rainfall as scripted.
5. Subscribed to `ui:EXC001` from a fresh worker start and observed exactly one `envelope` push: `{"active_conditions":["RAIN","MUDDY"],"red_radius_m":5.2,"orange_radius_m":9.1,"speed_cap_kmh":3.0,"condition_multiplier":1.3}` (base is 4.0/7.0/6.0 — confirms G5 end-to-end).
6. `XLEN events:{shard of EXC001}` grew to 220 during the run (confirms G1).
7. Queried `events` table: `SEATBELT_VIOLATION`/`PROXIMITY_BREACH` rows all carry `site_id="SITE-A"` and `operator_id="11111111-1111-1111-1111-111111111111"` (the seeded `operator` user) — confirms G2 and G9.
8. `HGETALL machine_state:EXC001` includes `ui_mode` (confirms G6 is wired; IDLE_HUB transition itself needs an idle-mode scenario run, deferred to the Batch 3I manual checklist).

**Known issues (unchanged, out of scope for this phase per the plan):**
- **G12**: the hot worker updates `machine_state:{id}.last_seq` before the DB flush, so events buffered at crash time can be lost on restart. This is a hot-path redesign, explicitly deferred.
- **G7/G11** (frontend: `risk` push payload shape, alert dedupe key): deferred to Batch 3I as planned.

**Architecture decisions / deviations:**
- Fixed the `publish_context` `AttributeError` bug (not in the original gap list — found during manual verification) because it silently blocked all context ingestion, which is required to verify G5.
- Fixed `simulator/scenarios/demo.yaml`'s incomplete `context:` block for the same reason.

**Next:** Batch 3A (Historical data + ML foundations), awaiting approval.

---

## Stage 3 / Batch 3A: Historical data + ML foundations  (2026-09-24)

**Status:** ✅ verified

**Goal:** a deterministic synthetic historical dataset (tasks + 300s window aggregates + weather/ground/site/queue + operator skill + ~3% injected anomalies), a shared feature module used by both training and serving, and a time-based train/val/test split — no models trained yet (that's 3B/3C).

**New files:**
- `ml/constants.py` — single source of truth for task types, duration multipliers, feature lists, factor groups. **Assumption flagged:** `GROUND_M` (ground-condition duration multiplier) is not specified in BUILD_PLAN/ARCH (which only treats ground as a safety-envelope condition, not a duration multiplier) — used `{DRY:1.0, WET:1.06, MUDDY:1.15, ICY:1.25}`.
- `ml/features.py` — `compute_window_features()` (pure numpy, no pandas — this will run inside the warm worker's hot loop in Batch 3D), `robust_z()`, `eta_feature_row()`/`encode_eta_row()`/`eta_feature_columns()` (used by Batch 3B).
- `ml/sim_physics.py` — numpy re-implementation of `simulator/sim.py::advance_state`'s per-tick physics (target-based EMA + Gaussian noise), used to generate frame-level telemetry for history without depending on the async/httpx-based real simulator. Supports anomaly injection (`fuel_multiplier`, `cycle_duration_range`, `target_temp_override`) and physical-state threading across window calls (`init_state`/return `final_state`) so state is continuous across a shift, not reset every 300 frames.
- `ml/generate_history.py` — day-by-day generator: per machine, a timeline of tasks (WORKING windows, duration via the BUILD_PLAN formula `base_min × weather_m × ground_m × skill_m × (1+0.03·age) × lognormal(0,0.08) + queue_delay`) separated by idle gaps whose physical signals (truck presence, queue length, forced machine fault, weather) are constructed to directly satisfy the Batch 3C attribution priority rule — the generator IS the ground truth for idle-cause accuracy evaluation. ~3% of eligible WORKING windows get one of `HIGH_FUEL_PER_CYCLE` / `ERRATIC_CYCLES` / `HOT_ENGINE` injected.
- `ml/requirements-train.txt` — exact pinned versions from this venv, for training on a different machine (e.g. Colab) without artifact-loading skew.
- `ml/tests/test_features.py` (11), `ml/tests/test_generate_history.py` (9), `ml/tests/test_sim_parity.py` (3).
- `Makefile`: `gen-data` / `gen-data-dev` / `gen-data-tiny` / `test-ml` targets.

**Two real bugs found and fixed by `test_sim_parity.py` (the train/serve-skew guard) before this batch was considered done:**
1. **Physical-state reset per window.** Every window originally started `simulate_window` at rpm=0/hyd=0/fuel=0, so EVERY 300-frame window included a spin-up transient — not just the first window of a shift. This inflated `rpm_std` by ~3x versus real continuously-running telemetry (real ≈68.8 via the AR(1) steady-state formula, generator was giving 190–250). Fixed by threading physical state (`init_state`/`final_state`) across windows within a machine's day, resetting only once per machine per day (shift start).
2. **Exponential fuel blowup in anomaly injection.** `fuel_multiplier` (used for the `HIGH_FUEL_PER_CYCLE` anomaly) was applied to the *persistent* EMA state every frame instead of only the reported reading, compounding to `1.6^300 ≈ 10^93` over a window (`fuel_per_cycle` max was `1.97e+93`). Fixed by multiplying only the per-frame output value, never feeding the multiplied value back into the carried state. Added `test_fuel_multiplier_does_not_compound_across_frames` as a regression guard. Post-fix: `HIGH_FUEL_PER_CYCLE` windows have `fuel_per_cycle` mean 0.60 vs normal-window mean 0.38 (the intended ~1.6x), no outliers.

**Tests executed (command + result):**
```
PYTHONPATH=. venv/bin/python -m pytest tests/ core/copilot_core/tests/ ml/tests/ -q
66 passed in 29.34s
```
(43 from Batch 3.0 + prior + 23 new: 11 features, 9 generator, 3 sim-parity — note test counts shifted slightly as the parity test file grew during the bug fixes above.)

**Manual verification / measured numbers (from `ml/data/manifest.json`, not estimated):**
| Profile | Days | Tasks | Windows | Generation time (this M1, base 8GB) |
|---|---|---|---|---|
| tiny | 3 | 139 | 2,133 | 2.6s |
| dev | 15 | 706 | 10,844 | 12.9s |
| **full** | **60** | **2,945** | **44,020** | **53.0s** |

Anomaly rate by split (full profile): train 2.55%, val 2.59%, test 2.33% — close to the ~3% target (BUILD_PLAN's own figure is approximate). Task count for "full" (2,945) came in lower than BUILD_PLAN's illustrative "~5,000" because the day-length cap (9h shift) limits how many ~14-task-per-day machine-days fit; this is the generator's own physically-grounded number, not forced to match the brief's figure — recorded here rather than overridden.

`ml/data/` (3.2MB, regenerable) is gitignored per the plan; `ml/artifacts/` is tracked (currently empty, `.gitkeep` only — Batch 3B/3C write model files there).

**Simplifications versus the fully-detailed plan (documented, not silently done):**
- Weather/ground context is drawn once per (site, day), not on an hourly Markov chain as ARCH §6 implies for live context — a coarser but still-varied and deterministic approximation, adequate for day-granularity WEATHER-cause attribution thresholds (rainfall/visibility/wind) in Batch 3C.
- Idle gaps are single-cause per window (not a fractional mix within one window) — this was a deliberate design choice (see "Architecture decisions" below), not a simplification forced by time pressure.

**Architecture decisions / deviations:**
- Each idle window's physical signals (truck presence, queue length, forced fault, weather) are constructed to encode exactly ONE idle cause per window, chosen by the generator itself via a cause-weighted draw. This makes the dataset simultaneously the training data AND the ground-truth labels for Batch 3C's idle-attribution accuracy evaluation, without a separate hand-labelling step. Trade-off: real idle periods can plausibly have mixed causes within one window; this generator does not model that.
- `PLANNED` break is inserted once per machine per day at a fixed elapsed time (4h in), not tied to a specific wall-clock time window — simpler than a global 12:00–12:30 UTC window while still being deterministic and evenly distributed.

**Next:** Batch 3B (ETA prediction + explainability + live blending), awaiting approval.

---

## Stage 3 / Batch 3B: ETA prediction + explainability + live blending  (2026-09-24)

**Status:** ✅ verified

**Goal:** train P50 (point) and P10/P90 (quantile) XGBoost ETA models, grouped SHAP factor explanations in minutes with a task-type background, and pure/tested `blend()` (live ETA update from observed progress) and `slip()` (ETA_SLIP threshold) functions. Wiring into the warm worker is Batch 3D — this batch is training + a standalone service layer only.

**New files:**
- `contracts/intelligence.py` — `EtaFactor`, `EtaEstimate` (additive; `UiPushType.eta` already existed from before Stage 3).
- `backend/app/services/ml_registry.py` — lazy singleton `ModelRegistry`/`get_registry()`. Loads `ml/artifacts/eta_*`, checks trained-vs-installed `xgboost`/`sklearn` major.minor versions, and degrades to `MISSING`/`VERSION_MISMATCH`/`LOAD_ERROR` instead of raising — every downstream caller (`eta.py`, and `attribution.py`/`anomaly.py` in Batch 3C) goes through this rather than loading files itself.
- `ml/train_eta.py` — trains `eta_p50.json` (`reg:absoluteerror`) and `eta_q.json` (`reg:quantileerror`, α=[0.1,0.9]) as portable XGBoost JSON, a per-task-type SHAP background sample (`eta_background.npz`, ≤100 rows/type), and `eta_meta.json` (model version, feature columns, factor groups, val MAE, lib versions).
- `backend/app/services/eta.py` — `predict_task()`, `explain()` (SHAP TreeExplainer, grouped into `ETA_FACTOR_GROUPS`), `blend()` (live ETA = model/observed weighted by progress), `slip()` (ETA_SLIP band + severity).
- `ml/tests/test_train_eta.py` (3), `backend/tests/unit/test_eta_service.py` (16).
- `Makefile`: `train-eta` target.
- `backend/app/config.py`: added `ml_artifacts_dir`, `eta_slip_threshold` (0.10), `eta_slip_warning_threshold` (0.25), `eta_min_cycles_for_blend` (3).

**Environment note (M1-specific):** XGBoost failed to import with `Library not loaded: @rpath/libomp.dylib` — this Mac had no OpenMP runtime installed system-wide (a pip-level dependency gap, `pip install xgboost` alone is not enough on macOS ARM). Fixed with `brew install libomp`. **This is a one-time host setup step, not a code change** — anyone else running this repo's ML code on macOS will need it too; recorded here so it isn't rediscovered.

**Bug found and fixed via the test suite (not just tuned away):** SHAP factors are rounded to 1 decimal place for display (ARCH 6.8: "expressed in meaningful units"), but summing several independently-rounded factors could drift up to ~0.12 min from the displayed p50 (`test_shap_additivity` caught this at 0.05 tolerance). ARCH 6.8 requires the displayed breakdown to sum **exactly**, so `predict_task()` now absorbs the rounding residual into the largest-magnitude factor after rounding, guaranteeing `base + Σ factors == p50` to float precision, not just approximately.

**Also found: SHAP `interventional` perturbation initially raised `NotImplementedError: Categorical split is not yet supported`** on this shap 0.52 + xgboost 3.4.1 combination, even with plain one-hot/numeric features (no pandas categorical dtype). Root cause: XGBoost's `enable_categorical` flag. Fixed by passing `enable_categorical=False` explicitly to `XGBRegressor` in `ml/train_eta.py` — without it, `SHAP TreeExplainer(..., feature_perturbation="interventional")` cannot be used at all on this stack, so this isn't optional tuning.

**Tests executed (command + result):**
```
PYTHONPATH=. venv/bin/python -m pytest tests/ core/copilot_core/tests/ ml/tests/ backend/tests/ -q
85 passed in 40.11s
```
(66 prior + 19 new: 3 `test_train_eta.py`, 16 `test_eta_service.py`.)

**Manual verification (measured):**
- `make train-eta` on the full 60-day dataset (`ml/data/`, 2,078 train / 277 val tasks): **0.85s** training time, `val_mae_p50 = 5.58 sim-minutes`. **This is a validation-set number, not test-set** — the honest test-set MAE (and MAE-vs-planner-estimate comparison) is Batch 3C's `evaluate.py`, per the plan.
- Live prediction sanity check (`ml/artifacts` loaded via the real registry):
  - SUNNY/EXPERT: P50=38.65 [36.83–52.53] min
  - RAIN+MUDDY, same task: P50=56.05 min (higher, correct direction)
  - BEGINNER vs EXPERT, same task: P50=54.6 vs 38.65 min (higher, correct direction)
  - Factor breakdown for SUNNY/EXPERT example: Weather −4.2, Ground −0.8, Operator −5.1, Machine −1.3, Site queue 0.0, Task scope +2.6 (all in minutes, sum to p50 exactly by construction).

**Artifact sizes:** `eta_p50.json` 1.4MB, `eta_q.json` 3.4MB, `eta_background.npz` 100KB, `eta_meta.json` 4KB — all committed to `ml/artifacts/` (small, needed at runtime, per the plan).

**Known limitations / deferred:**
- No online/incremental retraining — models are static artifacts until someone reruns `make train-eta`.
- `blend()`/`slip()` are pure functions, unit-tested in isolation; their wiring into a live task's running state (persisting `last_emitted_band`, computing `observed_cycle_s` from real window aggregates) happens in Batch 3D's warm worker, not here.

**Next:** Batch 3C (Idle attribution + anomaly detection + evaluation), awaiting approval.
