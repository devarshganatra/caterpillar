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

---

## Stage 3 / Batch 3C: Idle attribution + anomaly detection + evaluation  (2026-09-24)

**Status:** ✅ verified

**Goal:** deterministic idle attribution with operator-deviation flagging, Isolation Forest anomaly detection with percentile calibration + SHAP drivers + robust-z fallback, and honest test-split evaluation of ETA/anomaly/attribution — no numbers claimed from training data.

**New files:**
- `ml/baselines.py` — hierarchical idle-ratio baselines (operator×context → skill×context → task_type → global), written to `ml/artifacts/idle_baselines.json`.
- `ml/train_anomaly.py` — `IsolationForest(n_estimators=200, contamination=0.03)` on `WINDOW_FEATURES`, percentile-calibrated score (1001 train-set quantiles), per-feature robust stats for the fallback → `iforest.joblib`, `iforest_meta.json`, `window_stats.json`.
- `ml/evaluate.py` — computes ETA MAE-vs-planner, anomaly precision/recall/F1/PR-AUC (both IF and robust-z), and idle-cause confusion matrix, **all on the test split only** → `ml/artifacts/metrics.json`. Asserts `split == "test"` for every block before writing.
- `backend/app/services/attribution.py` — `classify_idle_frames()` (the PLANNED>MACHINE>WEATHER>SITE>OPERATOR priority rule, first-match-wins), `attribute_window()`, `operator_deviation()` (hierarchical backoff + persistence).
- `backend/app/services/anomaly.py` — `score_window()`: eligibility check → IsolationForest + SHAP drivers (cached explainer) → robust-z fallback → `UNAVAILABLE`. Never touches safety state.
- `contracts/intelligence.py`: added `IdleAttribution`, `AnomalyDriver`, `AnomalyResult`.
- `backend/app/services/ml_registry.py`: extended to load `iforest.joblib`/`iforest_meta.json`/`window_stats.json` (anomaly_status) and `idle_baselines.json` (baselines_status), same MISSING/VERSION_MISMATCH/LOAD_ERROR degradation pattern as ETA.
- `backend/app/config.py`: added `weather_stop_*`, `machine_fault_hold_s`, `idle_dev_*`, `idle_baseline_min_n`, `anomaly_threshold_pct`, `anomaly_min_train_rows`, `robust_z_threshold`.
- `ml/tests/test_baselines.py` (4), `ml/tests/test_train_anomaly.py` (4), `ml/tests/test_evaluate.py` (4), `backend/tests/unit/test_attribution.py` (13), `backend/tests/unit/test_anomaly_service.py` (9).
- `Makefile`: `baselines`, `train-anomaly`, `evaluate`, and composite `train` (= gen-data → train-eta → baselines → train-anomaly → evaluate, matching `BUILD_PLAN_new.md` §7).

**Real bug found and fixed:** `ml/baselines.py` originally produced **zero** entries at the `operator_context`/`skill_context`/`task_type` levels — only `global` populated. Root cause: idle-gap windows are generated with `task_type=""`, which `pandas.read_csv` reads back as `NaN`, and `pandas.groupby` **silently drops every row with NaN in a groupby key column by default**. Since every row used for these baselines is an idle window, 100% of the input got dropped at every non-global level. Fixed by filling `task_type` with a `"NONE"` sentinel before grouping. Caught by `test_all_levels_populated`, added specifically as a regression guard for this failure mode.

**Documented design limitation (not a bug):** because idle-gap windows have no associated task, the `task_type` dimension of "context" (ARCH 6.6: `context = (task_type, weather, skill)`) degenerates to a single `"NONE"` bucket at the `task_type` backoff level — it isn't a genuine per-task-type baseline for idle time. The `operator_context` and `skill_context` levels (which also include `weather`) remain meaningfully discriminative. A future iteration could associate idle gaps with their *preceding* task's type instead.

**Tests executed (command + result):**
```
PYTHONPATH=. venv/bin/python -m pytest tests/ core/copilot_core/tests/ ml/tests/ backend/tests/ -q
119 passed in 83.10s
```
(85 prior + 34 new.)

**Manual verification / measured numbers — `ml/artifacts/metrics.json` on the real test split (2,945-task / 44,020-window full dataset), pasted verbatim:**

*ETA (test, n=590 tasks):*
| Metric | Value |
|---|---|
| Model P50 MAE | **4.75 sim-min** |
| Planner-estimate MAE | 14.88 sim-min |
| Relative improvement vs planner | **+68.1%** (BUILD_PLAN target was ≥30%) |
| P10–P90 coverage | 74.4% (target ~80%; measured, not adjusted to hit the target) |

*Anomaly (test, n=7,137 eligible windows, 204 positive):*
| Method | Precision | Recall | F1 | PR-AUC |
|---|---|---|---|---|
| Isolation Forest | 0.120 | 0.132 | 0.126 | 0.111 |
| Robust-z fallback | 0.156 | 0.598 | 0.248 | 0.123 |

IF recall by injected type: `HIGH_FUEL_PER_CYCLE` 1.5%, `HOT_ENGINE` 10.9%, `ERRATIC_CYCLES` 26.8%.

**This is a genuinely weak result for the primary model, reported honestly rather than adjusted.** Investigated rather than hidden: the false positives (198 of them) have a median `rpm_std` of 142.9, versus 66.7 for the eligible-window population overall — they are dominated by the one-per-machine-per-day shift-start transient (Batch 3A's physics continuity fix: state resets once per day, and that reset genuinely produces a statistically unusual window). This competes with the intentionally-injected anomaly types for the Isolation Forest's fixed 3% contamination budget, so the model correctly finds outliers, just not preferentially the ones with `is_anomaly=1` labels. The calibration itself is correct (3.15% of test windows flagged, matching the 0.97 threshold). Options for a future iteration: exclude the first window of each machine-day from training, or accept shift-start flagging as a legitimate (if unlabeled) anomaly. Not fixed in this batch — recorded as a known, root-caused limitation.

*Attribution (test, n=1,423 idle windows):* primary-cause agreement **94.9%**. The confusion is concentrated in `WEATHER`: true-WEATHER windows are predicted WEATHER only 43% of the time (33/76), with SITE/OPERATOR splitting the rest. Root cause verified: the generator can choose the `WEATHER` gap cause with a small non-zero weight even on non-heavy-rain days (a modest 5–15 min stoppage), and on those days the window's `rainfall_mm_h`/`visibility_m`/`wind_kmh` don't cross the detection thresholds used by `classify_idle_frames` — so the window is physically indistinguishable from an `OPERATOR`/`SITE` window using only the signals the real attribution service has access to. This is a generator/evaluation-methodology characteristic (the ground-truth label encodes generator intent, not necessarily a detectable physical signal), not a classifier defect.

**Architecture decisions / deviations:**
- `evaluate_attribution()` re-derives the predicted cause from the window's own aggregate CSV columns (not by re-running `classify_idle_frames()` frame-by-frame, since the CSV doesn't retain per-frame arrays) — this tests the same priority *logic*, applied at window granularity.
- `np.trapz` was removed in numpy 2.x (renamed `np.trapezoid`); `ml/evaluate.py`'s PR-AUC helper handles both.

**Next:** Batch 3D (Warm worker + window processing), awaiting approval.

---

## Stage 3 / Batch 3D: Warm worker + window processing  (2026-09-24)

**Status:** ✅ verified

**Goal:** a separate, restart-safe worker that consumes `telemetry:{shard}` on its own consumer group (`warm-worker`), builds 30s event-time windows per machine, runs ETA/attribution/anomaly on each, persists results, emits `OPERATIONAL_ANOMALY`/`IDLE_DEVIATION`/`ETA_SLIP` events to `events:{shard}`, and pushes `eta`/`idle_attribution`/`anomaly` UI updates — without ever touching hot-path safety state.

**New/changed DB schema** (migration `d6114ec31a5b_stage3_warm_path`, applied and round-trip tested up/down):
- `users.skill_level` (nullable) — ETA/attribution default to `INTERMEDIATE` when unset.
- `tasks.task_type`, `tasks.target_cycles`, `tasks.planned_start` (nullable) — ETA requires both `task_type` and `target_cycles`; a task missing either gets `EtaEstimate(status="UNAVAILABLE", unavailable_reason="TASK_METADATA_MISSING")`.
- `window_aggregates` (PK `window_id`, deterministic via `contracts.ids`) — one row per closed window: features, `context`, `idle_attribution`, `anomaly` (all JSONB), plus `deviation_raw_flag`/`deviation_consecutive`/`last_eta_slip_band` for restart-safe persistence of the deviation-persistence and slip-band state across windows (no in-memory-only counters).
- `eta_estimates` (`kind` = `BASELINE`|`LIVE`) — a **partial unique index** `(task_id) WHERE kind='BASELINE'` was added on top of the plan's `(task_id, window_id, kind)` unique constraint, because Postgres treats `NULL != NULL` and `window_id` is always `NULL` for `BASELINE` rows — the plain constraint alone would not have stopped two baseline rows for the same task.
- `seed_db.py`: `operator` now gets `skill_level="INTERMEDIATE"`; `TASK-001`/`TASK-002` now carry `task_type`/`target_cycles`/`planned_start` so the demo tasks actually get ETA predictions.

**New files:**
- `backend/app/worker/warm.py` — `OpenWindow`, `WarmProcessor` (`handle_message`, `close_window`, `flush_stale`, `recover`), `main()` (4-shard consumer-group loop, own pending-replay on start, exponential backoff on error).
- `backend/tests/unit/test_window_aggregation.py` (7) — window-boundary logic with a stubbed `close_window` (no DB needed).
- `tests/integration/test_warm_worker.py` (6) — full `close_window` pipeline against real Postgres+Redis+`ml/artifacts`.

**Two real bugs found and fixed before this batch was considered done (both concurrency/idempotency correctness issues, not cosmetic):**
1. **Machine-wide event collision.** The initial `_insert_event` implementation used a constant `frame_seq=0` for all warm-generated events. Since the DB's idempotency constraint is `(machine_id, frame_seq, type)`, every subsequent `OPERATIONAL_ANOMALY` (or any other warm event type) for the same machine would have collided with the first one ever inserted and been silently dropped by `ON CONFLICT DO NOTHING` — the plan's own spec ("frame_seq = last_seq of the window") was correct and I'd initially missed implementing it; caught by re-reading the plan against the code rather than by a test (worth flagging: this is exactly the kind of bug that only shows up after the SECOND anomaly on the same machine, so a single-window integration test would not have caught it either).
2. **Race condition via shared mutable state.** The ETA slip band was initially passed from `_compute_eta` to `_full_row` via a `self._last_slip_band` instance attribute on `WarmProcessor` — but different machines' `close_window()` calls run concurrently as separate asyncio tasks against the *same* `WarmProcessor` instance (one per shard), so one machine's band could be overwritten by another's before it was read back. Fixed by returning the band explicitly through the call chain instead of stashing it on `self`.

**Tests executed (command + result):**
```
PYTHONPATH=. venv/bin/python -m pytest tests/ core/copilot_core/tests/ ml/tests/ backend/tests/ -q
132 passed in 86.70s
```
(119 prior + 13 new: 7 unit, 6 integration.)

**Manual verification — full stack (API + hot + warm workers + simulator), measured:**
1. Ran the real `demo.yaml` scenario against `EXC001` for ~30s wall-clock (SIM_SPEED=10). Two windows closed with real inference: `latency_ms` 366 and 547 (first-call model/explainer construction overhead included).
2. Idle attribution: window 1 → `primary_cause: SITE` (44s SITE / 31s OPERATOR, low truck presence early on); window 2 → `primary_cause: OPERATOR` (90s SITE / 183s OPERATOR) — both with real `weather: RAIN` context from the scenario's context frame, confirming the warm worker reads the same `context:{site_id}` hash Batch 3.0 wired up.
3. ETA: exactly 1 `BASELINE` row and one `LIVE` row per window (2 `LIVE` rows total), factors correctly attributing +7.5 min to Weather (rainfall_mm_h=12) for this task.
4. Anomaly: window 1 → `SKIPPED/NOT_WORKING_WINDOW` (machine still idle); window 2 → `IFOREST`, score 0.985 ≥ 0.97 threshold, `is_anomalous=true`, top driver `temp_slope` (z=18.4) — this is the **same cold-start artifact documented in Batch 3C's evaluation** (the first working window after a machine starts genuinely has an unusual temp_slope), now observed live in the real pipeline rather than just in offline evaluation — consistent, not a new bug.
5. Confirmed the `OPERATIONAL_ANOMALY` event landed on `events:1` (EXC001's shard) with the correct `source_engine: anomaly@iforest-...` and full driver evidence, ready for the correlator (Batch 3E).
6. **Restart test**: cleared DB, ran the simulator, `kill -9` the warm worker mid-window at t=4s. `XPENDING telemetry:1 warm-worker` confirmed 32 delivered-but-unacked messages at that instant. Restarted the worker (fresh process, `recover()` from DB): it replayed the 32 pending entries automatically, closed 2 windows totalling 106 frames, `XPENDING` drained to 0. Verified in `window_aggregates`: exactly 2 rows, contiguous `first_seq`/`last_seq` (1→86, 87→106) — **no duplicate rows, no gap, no double-count** despite the hard kill.
7. `worker:status:warm` hash confirmed populated (`last_ok_ts`, `last_latency_ms`).

**Known limitations / deferred:**
- `classify_idle_frames` is called with a synthetic `ts_start + i*sim_seconds_per_frame` timestamp grid rather than each frame's own (slightly jittery) real timestamp, for the `PLANNED` break check specifically — documented in the plan as an acceptable approximation; not revisited here.
- The non-authoritative per-frame `is_idle` signal (feeding `idle_ratio`) is derived directly from `hydraulic_pressure_bar < 50 and speed < 0.5`, not by calling `core.copilot_core.state.classify_state` sequentially as the plan originally sketched — this avoids needing to carry hysteresis/dwell state across windows per machine (another source of restart-complexity) for a feature that's explicitly non-safety-critical and already documented as an approximation. If dwell-accurate idle classification turns out to matter for a later batch, it can be added without changing the warm worker's persisted schema.
- Full-throughput latency (largest window, 273 frames) was 547ms — fine for a 30s window cadence, but not yet load-tested at the 500-machine scale ARCH's back-of-envelope section discusses.

**Next:** Batch 3E (Event correlation + incident persistence), awaiting approval.

---

## Stage 3 / Batch 3E: Event correlation + incident persistence  (2026-09-24)

**Status:** ✅ verified

**Goal:** a separate correlator service consuming `events:{shard}` on its own group, grouping events into Incidents per ARCH §6.9 (5-min per-machine correlation window, open/extend/escalate, ordered+bounded timeline, no duplicates). The warm worker never creates incidents.

**New DB schema** (migration `49f6179bb7ee_stage3_incidents`, up/down round-trip verified): `incidents` (deterministic PK via `contracts.ids.incident_id`), `incident_events` (`event_id` UNIQUE — one event belongs to at most one incident), `incident_timeline` (bounded via `(incident_id, entry_key, first_ts)` unique + merge-on-write, so repeated hot-path events collapse into one row with a growing `count`).

**New files:**
- `backend/app/services/correlator.py` — pure decision logic: `decide()` (OPEN/EXTEND/IGNORE_INFO + escalation rule), `timeline_entry_key()`/`merge_into_entry()` (bounds timeline growth), `summarize_event()` (deterministic per-type templates).
- `backend/app/worker/correlator.py` — `Correlator.handle_event()` (per-machine advisory lock → dedup check → decide → open/extend → link event → upsert timeline, one transaction), startup `_reconcile()` (catches the crash window between a producer's commit and its XADD), 4-shard consumer-group loop.
- `contracts/events.py`: `Incident` gained additive optional fields (`site_id`, `status`, `task_id`, `escalated`, `last_event_at`, `event_count`, `explanation_status`). `contracts/intelligence.py`: `IncidentSummary`, `TimelineEntry`.
- Tests: `backend/tests/unit/test_correlator.py` (19, pure decision logic), `tests/integration/test_correlator_worker.py` (10, full DB pipeline), `backend/tests/unit/test_entrypoint_shard_scoping.py` (1, regression guard — see below).

**Three real bugs found and fixed, in order of how they surfaced (all during manual end-to-end verification, not caught by any unit/integration test beforehand — worth being honest about that):**

1. **My own bug in `_extend_incident`.** I mutated `incident.severity` to the new max *before* comparing it against the old value to decide whether `category` should update — so the comparison was checking the new value against itself, always true. Fixed by capturing `old_severity` first. Caught by re-reading my own diff, not a test.

2. **`hot.py`'s event insert only covered one named constraint.** `on_conflict_do_nothing(constraint="uq_events_machine_seq_type")` — but Batch 3.0 made event IDs deterministic, so a machine's telemetry seq counter restarting (e.g. re-running the simulator) can regenerate a **primary-key** collision, which that narrowly-scoped clause doesn't catch. Result: an unhandled `IntegrityError` silently killed that shard's hot-worker consumer loop. Found by running two consecutive simulator sessions against the real stack and watching the hot worker log an uncaught exception. Fixed by dropping the conflict target entirely (`on_conflict_do_nothing()`, no args) — matches the pattern already used in `warm.py`/`correlator.py`.

3. **A genuine pre-existing concurrency bug in `entrypoint.py`, exposed (not created) by this batch.** The idle-flush branch iterated `hot_states.values()` — *every* machine, shared across all 4 concurrent shard-consumer tasks — instead of only the machines belonging to that task's own shard. Two shard tasks could call `flush_buffers()` on the *same* `MachineHotState` concurrently, racing on its mutable buffer lists. Effect: some events got XADDed to `events:{shard}` (from one racing copy) but never actually committed to the DB (lost in the other copy) — invisible until the correlator tried to link one and hit a foreign-key violation on an event that plain doesn't exist. This predates Stage 3 entirely; nothing before the correlator was checking referential integrity against the stream, so it was silently losing data with no symptom. Fixed by scoping the idle-flush loop to `shard_for(machine_id) == shard_index`, extracted into a testable `flush_idle_machines_for_shard()` helper with a regression test (`test_entrypoint_shard_scoping.py`) that fails on the pre-fix code (verified by construction: without the filter, the mocked flush would be called for both shards' machines).

**Tests executed (command + result):**
```
PYTHONPATH=. venv/bin/python -m pytest tests/ core/copilot_core/tests/ ml/tests/ backend/tests/ -q
162 passed in 85.92s
```
(132 prior + 30 new: 19 correlator unit, 10 correlator integration, 1 entrypoint regression.)

**Manual verification — full stack (API + hot + warm + correlator + simulator), measured:**
1. Ran `demo.yaml` against `EXC001` (seatbelt at 30s + 4× ORANGE proximity repeats) for ~15s. Correlator log showed a stream of `extended`/`escalated` actions, all against the same `incident_id`.
2. Final incident: `severity=CRITICAL`, `escalated=true`, `status=OPEN`, `event_count=138`, `category=SEATBELT_VIOLATION` (the original trigger's type, correctly preserved since the CRITICAL upgrade came from the SAME type).
3. Timeline: **138 individual events collapsed into exactly 3 timeline rows** — 1 `SEATBELT_VIOLATION` entry (`count=44`), 1 `STATUS` escalation entry, 1 `PROXIMITY_BREACH:ORANGE` entry (`count=94`) — the bounded-timeline merge logic working correctly on real, continuous hot-path event volume.
4. `incidents:work` stream correctly received `opened` then `escalated` entries (payload: `incident_id`, `reason`) for the Batch 3H cold worker to consume later.
5. **Referential integrity check** after the two-bug-fix cycle above: `SELECT count(*) FROM incident_events WHERE event_id NOT IN (SELECT id FROM events)` → **0** (was >0 before the fixes, causing live `ForeignKeyViolationError`s).
6. Ran two back-to-back simulator sessions (same scenario, different seeds — deliberately reproducing overlapping deterministic event IDs, since the scenario's frame-seq timing is independent of RNG seed) specifically to stress the idempotency/conflict-handling path: zero errors in hot worker or correlator logs after the fixes, versus reliable crashes before them.

**Known limitations / deferred:**
- The plan's "two correlator consumers racing on the same machine → exactly one incident" test is covered structurally by the advisory lock (`pg_advisory_xact_lock(hashtext('corr:'||machine_id))`) and by the deterministic-incident-id + `ON CONFLICT DO NOTHING` combination, but not exercised with two genuinely concurrent asyncio tasks in a test — the lock serializes any such race by construction, and this pattern already has coverage in Batch 3D's warm-worker equivalent.
- `_lookup_task_id` picks the most recent `window_aggregates` row at or before the event's timestamp; if the warm worker hasn't closed a window yet for a brand-new task, an incident opens with `task_id=None` until one does. Not revisited — acceptable given incidents open on safety events that fire well before a 30s window closes anyway.

**Next:** Batch 3F (Incident APIs + snapshot + backend integration), awaiting approval.

---

## Stage 3 / Batch 3F: Incident APIs + snapshot + backend integration  (2026-09-24)

**Status:** ✅ verified

**Goal:** RBAC-protected incident APIs with audited mutations, a machine snapshot endpoint for WS reconnect, ETA/window read endpoints, and a worker-status endpoint.

**New endpoint list:**
| Endpoint | Roles | Notes |
|---|---|---|
| `GET /incidents` | any | OPERATOR: own only; SUPERVISOR: their sites only (403 if `site_id` filter is out of scope); ADMIN: all |
| `GET /incidents/{id}` | any, scoped | 404 missing, 403 out of scope; includes `explanation` (null until Batch 3H) |
| `GET /incidents/{id}/timeline` | any, scoped | ordered `(first_ts, id)`, each with `representative_event` |
| `POST /incidents/{id}/ack` | SUPERVISOR, ADMIN | OPEN→ACKNOWLEDGED only, else 409; audited `INCIDENT_ACK` |
| `POST /incidents/{id}/close` | SUPERVISOR, ADMIN | OPEN\|ACKNOWLEDGED→CLOSED only, else 409; audited `INCIDENT_CLOSE` (payload carries `note_sha256`, not the note text) |
| `GET /machines/{id}/snapshot` | any, site-scoped | hot state, envelope, context, recent events, latest window, ETA, open incidents — every field from Redis/DB, explicit null/UNAVAILABLE otherwise |
| `GET /machines/{id}/windows` | any, site-scoped | recent `window_aggregates` + `totals_s` per idle cause |
| `GET /tasks/{task_id}/eta` | own/site-scoped | latest LIVE, else BASELINE, else UNAVAILABLE |
| `GET /admin/workers` | ADMIN | `worker:status:*` + consumer-group lag |

**Files:** `backend/app/api/incidents.py`, `backend/app/api/machines.py`, `backend/app/api/admin.py` (new); `tasks.py`, `main.py` (modified, router registration); `contracts/openapi.md` (corrected stale paths from earlier stages and documented every new endpoint + `UiPush` payload shape).

**RBAC table** (verified by `tests/integration/test_incident_api.py`):
| Actor | Own incident | Other operator's / out-of-scope site | Admin |
|---|---|---|---|
| OPERATOR | 200 | 403 | — |
| SUPERVISOR (their site) | 200 | 403 (other site) | — |
| ADMIN | 200 | 200 | 200 |
| unauthenticated | 401 | 401 | 401 |

**Tests executed (command + result):**
```
PYTHONPATH=. venv/bin/python -m pytest tests/integration/test_incident_api.py tests/integration/test_snapshot.py -q
16 passed in 3.49s
```
(10 incident API, 6 snapshot — targeted runs per current test-suite-cadence preference; full-suite run deferred to the next natural checkpoint.)

**Two real issues found and fixed while writing the tests (both about how `TestClient` behaves, not app-code bugs — but worth recording since they'd trip up anyone extending this suite):**
1. `TestClient(app)` constructed at module scope — the pattern this repo already uses in `tests/test_batch4.py` — does **not** run FastAPI's `lifespan` handler unless used as a `with` block. `main.py`'s `stream.redis_client = redis.from_url(...)` therefore never executes, so any Redis-dependent endpoint (snapshot) 503'd in tests. Fixed with an explicit per-test fixture that sets `stream.redis_client` directly, mirroring what the lifespan does in production.
2. That fixture had to be **function-scoped, not module-level**: a `redis.asyncio.Redis` client's connection binds to whatever event loop is running when first used, and pytest-asyncio gives each async test its own loop — sharing one client across tests raised `RuntimeError: Event loop is closed`. The fixture's teardown `aclose()` can still hit this harmlessly (`TestClient`'s synchronous request runs the ASGI app through its own internal loop, separate from the test's), so that specific `RuntimeError` is caught and ignored at teardown only — never around the actual assertions.

**Also discovered:** `tests/test_batch4.py::test_audit_tamper_evidence` (existing code, not written this session) deliberately corrupts `audit_log` and never restores it, by design (it's testing tamper *detection*). This silently poisons `GET /audit/verify` for any test that runs afterward in the same DB and expects a valid chain. Fixed by having the new ack/close audit test `TRUNCATE TABLE audit_log` at its own start — the same convention that test itself already uses — rather than assuming a clean starting state.

**Manual verification (curl, full flow, real stack — API + hot worker + correlator + simulator):**
1. `GET /incidents` (supervisor) → one incident from a live demo run (`CRITICAL`, `escalated: true`, `event_count: 113`).
2. `GET /incidents/{id}/timeline` → 3 ordered entries (`SEATBELT_VIOLATION` ×45, `STATUS: escalated`, `PROXIMITY_BREACH:ORANGE` ×68) from the 113 raw events — bounded-timeline merge confirmed on a fresh scenario run.
3. `POST /ack` → `{"status": "ACKNOWLEDGED"}`; `POST /close` (with note) → `{"status": "CLOSED"}`; a second close → `409`.
4. `GET /audit/verify` → `{"valid": true}` after ack+close (confirmed on a freshly-reset chain, since the dev DB's audit_log carried pre-existing pollution from the `test_batch4.py` tamper test described above — the automated `test_ack_then_close_with_audit_trail` test proves this same flow with real assertions, including checking for the specific `INCIDENT_ACK`/`INCIDENT_CLOSE` rows).
5. Snapshot with no data → explicit `null`/`UNAVAILABLE` fields, nothing invented. After stopping the simulator for >10s → `hot.stale: true`.

**Known limitations / deferred:**
- `GET /incidents/{id}` looks for an `IncidentExplanationRow` inside a `try/except ImportError`, since that table doesn't exist until Batch 3H — this is intentionally forward-compatible rather than a stub to revisit.
- No pagination cursor beyond `limit`/`offset` on `GET /incidents` — fine at this scale, would need a real cursor for production incident volume.

**Next:** Batch 3G (Knowledge retrieval + Gemini client), awaiting approval.

---

## Stage 3 / Batch 3G: Knowledge retrieval + LLM client  (2026-09-24)

**Status:** ✅ verified

**Goal:** a stable-ID knowledge base with BM25 retrieval, the incident packet builder, a structured-output schema, and an async LLM client with timeout.

**Architecture decision — Gemini → Groq.** The plan originally scoped this batch around Gemini (`google-genai` SDK). Partway through, the user provided a Groq API key and asked to use it instead, picking a model for "text generation only, no very big tough task" with good rate limits. Checked the live Groq API: the model catalog no longer includes Llama 3.x (the user's first guess) — it now serves OpenAI's open-weight `gpt-oss-20b`/`gpt-oss-120b` and Qwen. Chose **`openai/gpt-oss-20b`**: identical rate-limit budget to `gpt-oss-120b` on this key (1000 req / 8000 tokens per reset window), but faster (better fit for the 8s timeout) and cheaper on tokens per call. Model name is entirely config-driven (`settings.groq_model`), never hardcoded elsewhere, so switching later is a one-line change. This is a **provider swap, not a scope change** — every other part of the plan (packet-only input, no tool calling, grounding validation, deterministic fallback in 3H) is unchanged; `generate_explanation(system, user) -> (dict, meta)` is provider-agnostic by design.

**Key handling:** the real key was pasted in chat and written directly to `.env` (confirmed gitignored, never committed, never echoed in any output); `.env.example` has an empty placeholder.

**New files:**
- `backend/app/knowledge/docs/*.md` — 10 self-written docs (paraphrased general best practice, not copied from any Cat manual), **48 total chunks**: `seatbelt-safety`, `proximity-awareness`, `adverse-weather-operation`, `idle-management`, `fuel-efficiency`, `pre-start-inspection`, `heat-stress`, `excavation-basics`, `machine-health-warnings`, `truck-loading-coordination`.
- `backend/app/knowledge/retriever.py` — `load_chunks()` (YAML front matter + `##`-split, `chunk_id = f"{doc_id}#{slugify(heading)}"`, fails loudly on duplicates), `KnowledgeRetriever` (BM25 via `rank_bm25`, tag match ×1.5 boost, deterministic tie-break by `chunk_id`), `query_for_incident()`.
- `backend/app/api/knowledge.py` — `GET /knowledge/chunks/{chunk_id}` for citation chips.
- `backend/app/genai/schemas.py` — `IncidentExplanationLLM` (Pydantic, used both to constrain the LLM's output and to validate what comes back) + `llm_json_schema()` (hand-flattened JSON Schema — see bug below for why not `.model_json_schema()` directly).
- `backend/app/genai/packet.py` — `build_incident_packet()` (bounded, deterministic: timeline ≤`packet_max_timeline`, events ≤`packet_max_events` with a per-event-type evidence whitelist, machine/task/ETA/context/attribution/history/knowledge, explicit `allowed_event_ids`/`allowed_chunk_ids`, `operator_id` as an opaque string only), `packet_hash()`.
- `backend/app/genai/prompts.py` — `PROMPT_VERSION`, `SYSTEM_PROMPT` (packet-only, probable-cause wording, treats packet content as data not instructions), `build_user_prompt()`, `build_retry_prompt()`.
- `backend/app/genai/client.py` (rewritten from stubs) — `generate_explanation()` using `AsyncGroq`, `LLMUnavailable(reason)` (`NO_API_KEY|TIMEOUT|API_ERROR|MALFORMED`); no key → no network call; never logs the key or full prompt.
- `scripts/try_llm.py` — one-off manual verification script (key never printed).
- Tests: `backend/tests/unit/test_retriever.py` (11), `test_llm_client.py` (7, renamed from the plan's `test_gemini_client.py`), `test_packet.py` (6, integration-flavored, needs DB).

**Two real bugs found — one via the offline test suite, one only surfaced by calling the real API:**
1. **Timeline truncation could return *more* entries than the configured max, with duplicates.** `keep_recent = packet_max_timeline - 10` goes negative when the max is configured below 10 (exercised by a test setting `packet_max_timeline=5`); the resulting negative-index slice wrapped around and returned overlapping head/tail entries — 11 entries for a max of 5. Fixed by capping the head portion at `min(10, packet_max_timeline)` and only taking a tail slice when there's budget left for one.
2. **The JSON Schema sent to Groq didn't encode array length bounds.** `llm_json_schema()` only had `"type": "array", "items": {...}}` for the `*_refs` fields — no `minItems`/`maxItems` — so the model was free to return e.g. 5 `lesson.knowledge_refs` while `IncidentExplanationLLM`'s own `Field(max_length=3)` then rejected it. Found by running `scripts/try_llm.py` against a real incident with the real API (not by any test — the mocked client tests all use fixed, already-valid response fixtures, so this gap was invisible to them). Confirmed Groq's strict `json_schema` mode does respect `minItems`/`maxItems`/`maxLength` once added, by rerunning the same script.

**Tests executed (command + result):**
```
PYTHONPATH=. venv/bin/python -m pytest backend/tests/unit/test_retriever.py backend/tests/unit/test_llm_client.py backend/tests/unit/test_packet.py -q
24 passed in 1.39s
```
(targeted run per current test-suite-cadence preference; full-suite run deferred to the next natural checkpoint.)

**Manual verification — real Groq API call against a real incident (`scripts/try_llm.py`), after the schema fix:**
- `model=openai/gpt-oss-20b`, `latency_ms=2476.7` (well inside the 8s timeout budget), `usage: {prompt_tokens: 3026, completion_tokens: 1332}`.
- Output validated cleanly against `IncidentExplanationLLM`. Every `evidence_refs` entry was one of the packet's 2 `allowed_event_ids`; every `knowledge_refs`/`training_refs` entry was one of the packet's 5 `allowed_chunk_ids` — zero invented references, unprompted (no retry needed to achieve this).
- Language stayed correctly hedged throughout ("probable cause", "may have contributed") — no "caused" or "root cause is" phrasing, matching the system prompt's rule, again without needing a retry.
- Confirmed earlier, before the schema fix: the *first* real-API call (max-item bounds missing) genuinely failed Pydantic validation with 5 `lesson.knowledge_refs` against a max of 3 — this is exactly the class of failure Batch 3H's retry-once-then-fallback exists to handle, and it's now less likely to trigger on the first attempt.

**Known limitations / deferred:**
- No tool calling, per the phase requirement — the packet is the only input the model ever sees.
- Grounding *validation* (rejecting a response that cites an id outside `allowed_event_ids`/`allowed_chunk_ids`, the retry loop, and the deterministic fallback) is Batch 3H, not this one — this batch only confirms the model *tends* to stay grounded when asked to, not that anything enforces it yet.
- `KnowledgeRetriever` is currently instantiated fresh per-process (module-level lazy singleton in `api/knowledge.py`); the cold worker (3H) will need its own instance — trivial, since construction is cheap (48 chunks, <1ms to load).

**Next:** Batch 3H (Cold worker + grounding + fallback), awaiting approval.

---

## Stage 3 / Batch 3H: Cold worker + grounding validation + deterministic fallback  (2026-09-24)

**Status:** ✅ verified

**Goal:** a cold worker that turns every opened/escalated incident into a grounded Groq explanation or a clearly-labelled deterministic fallback, isolated from every other path, with mandatory grounding validation and a retry-once-then-fallback policy.

**New DB schema:** `incident_explanations` (migration `082f4bce7d81_stage3_explanations`, up/down round-trip verified) — `source` (`GROQ|FALLBACK`), `packet_hash` unique per incident (idempotent reprocessing), `grounding` JSONB (`{valid, violations[], attempts}`), `fallback_reason`, `latency_ms`.

**New files:**
- `backend/app/genai/validator.py` — `validate_grounding()` (every `evidence_refs`/`knowledge_refs`/`training_refs` must exist in the packet's allowed sets; regex-based smuggled-reference detection in free text; forbidden-phrase rejection for "proven"/"definitely caused"/"root cause is"), `parse_and_validate()` (schema errors prefixed `schema:`).
- `backend/app/genai/fallback.py` — `build_fallback()`: deterministic, static per-event-type cause/action text, top-tag-matched knowledge ref, byte-identical output for the same packet.
- `backend/app/worker/cold.py` — `ColdWorker.process()`: cache check (packet_hash) → no key → fallback; else attempt → validate → retry once with violations fed back → fallback; persists, updates `incidents.explanation_status`, writes an `EXPLANATION`-kind timeline entry, publishes `ui:{machine}` `incident{action:"explained"}`, updates `worker:status:cold`. Any unexpected exception still tries to mark the incident `FAILED` rather than leaving it silently unexplained.
- `POST /incidents/{id}/explain` (SUPERVISOR/ADMIN) — re-queues via `incidents:work`, audited `INCIDENT_EXPLAIN_REQUEST`.
- `GET /incidents/{id}` now returns the real explanation (previously a `try/except ImportError` stub from Batch 3F, now the genuine query).
- Tests: `backend/tests/unit/test_validator.py` (10), `test_fallback.py` (9), `tests/integration/test_cold_worker.py` (8, includes a grep/AST-based "cold worker imported by nothing safety-critical" check across hot/warm/correlator/entrypoint/copilot_core).

**Three real bugs found — two only surfaced by writing/running the tests, one specifically only by calling the real API:**
1. **`ProbableCause.evidence_refs` has `min_length=1`, but two fallback code paths could construct one with `evidence_refs=[]`** (the "no distinct event types at all" edge case, and a defensive `if rep_id else []`) — would have crashed `build_fallback` itself in that rare case, exactly when the system most needs a working fallback. Fixed by threading the incident's own `trigger_event_id` (FK-guaranteed to exist) through as the ultimate fallback reference, added to `packet["allowed_event_ids"]` explicitly for this purpose.
2. **The packet's timeline query selected every `IncidentTimeline` kind, not just `EVENT`** (a Batch 3G bug, invisible until this batch because nothing before it wrote non-`EVENT` timeline rows). Once the cold worker persists an `EXPLANATION`-kind entry, a second packet build for the *same* incident state now included that new row and produced a different `packet_hash` — silently defeating the caching this same worker depends on to avoid double-calling the LLM. Caught by `test_same_packet_processed_twice_is_cached` actually failing (`GROQ` twice instead of `GROQ` then `CACHED`). Fixed by filtering the packet's timeline query to `kind == "EVENT"`, matching the plan's own spec ("timeline: ≤ packet_max_timeline **EVENT** entries") that I'd implemented too loosely the first time.
3. **A stale test fixture broke under the new FK.** `test_correlator_worker.py`'s `clean_machine` fixture deleted `IncidentRow` for `EXC001` without first deleting from the new `incident_explanations` table — a real row left over from this batch's own manual verification (see below) triggered a live `ForeignKeyViolationError` in the full suite run. Fixed by extending that fixture's cleanup, same pattern it already uses for the other incident-linked tables.

**Tests executed (command + result):**
```
PYTHONPATH=. venv/bin/python -m pytest tests/ core/copilot_core/tests/ ml/tests/ backend/tests/ -q
229 passed in 92.27s
```
(161 prior + 68 new: 10 validator, 9 fallback, 8 cold worker integration + the fixture fix above surfaced during this full run — everything green.)

**Manual verification (a): no API key → fallback.** Seeded a fresh incident (`HEALTH_THRESHOLD`, engine_temp_c=110), ran `ColdWorker.process()` with `groq_api_key=""`. `GET /incidents/{id}` via the real running API confirmed: `explanation_status: "FALLBACK"`, `source: "FALLBACK"`, `fallback_reason: "NO_API_KEY"`, `confidence: "LOW"`, evidence_refs/knowledge_refs all real and grounded.

**Manual verification (b): real key → real Groq explanation.** Ran `ColdWorker.process()` against the real incident from Batch 3F/3G's demo run (113 events, CRITICAL, escalated). `GET /incidents/{id}` confirmed: `source: "GROQ"`, `model_name: "openai/gpt-oss-20b"`, `explanation_status: "READY"`. Every `evidence_ref` resolved to a real event id; every `knowledge_ref`/`training_ref` resolved to a real chunk id — the model even correctly folded the incident's RAIN/MUDDY context into its `recommended_actions` (citing `adverse-weather-operation#rain-and-ground-condition`) without being explicitly told to look there. `grep -i "gsk_" /tmp/*.log` → 0 matches across every log file from this session's manual runs — the key never appeared anywhere.

**Known limitations / deferred:**
- `build_fallback()`'s `lesson` field picks the top-ranked retrieved chunk overall (per the plan's literal spec: "top-1 knowledge chunk"), not tag-filtered like `recommended_actions` is — observed live where a HEALTH_THRESHOLD incident's fallback lesson picked a weather-related chunk because leftover Redis context state (`RAIN`/`MUDDY`) from an much earlier manual session dominated the BM25 query. This is the retrieval mechanism working as designed, not a bug — flagging because it's a plausible future quality improvement (tag-filter the lesson pick too) if fallback lesson relevance turns out to matter more than the plan currently specifies.
- `build_fallback()` raises `RuntimeError` if the packet's knowledge list is completely empty (impossible with the real 48-chunk knowledge base, but a real failure mode if the knowledge directory were ever misconfigured) — deliberately loud rather than silently constructing a schema-invalid `Lesson`, tested in `test_fallback_raises_clearly_when_knowledge_base_empty`.

**Next:** Batch 3I (Frontend intelligence integration), awaiting approval.

---

## Stage 3 / Batch 3I: Frontend intelligence integration  (2026-09-24)

**Status:** ✅ verified

**Goal:** wire the ML/LLM intelligence layer (ETA, idle attribution, anomaly, incidents) into the React UI, reusing the app's existing dark/glass-card/CAT-yellow design language rather than a bolted-on new style, per the explicit brief: "very very good ui very smooth elegant impressive and shouldnt look like ai slop."

**New shared hook — `web/src/store/useMachineStream.ts`:** the single source of truth for one machine's live state (connection lifecycle, snapshot hydration, WS push handling, stale watchdog, exponential-backoff reconnect), now shared by both `MachineContext` (the operator's own machine) and `MachineCard` (a supervisor's fleet grid), which previously had two independently-drifting WebSocket implementations. Fixes three real bugs that existed in the pre-3I frontend, none caught until this batch actually read the wire contract against the backend's real payloads:
- **G6:** `Hud.tsx`/`IdleHub.tsx` navigated on `currentState === 'IDLE_HUB'`, a value the backend never sends — `state` and `ui_mode` are separate fields, and only `ui_mode` carries `IDLE_HUB`. The HUD ↔ Idle Hub transition was silently dead code before this batch.
- **G7:** the old `MachineCard.tsx` read `risk` pushes with a `.level` field the hot worker never emits; risk level came from `state_change.payload.risk_level` all along.
- **G11:** alert de-dupe/removal keyed on `alert.id`, which does not exist on the wire payload (`event_id` does) — every alert was being treated as new, and `alert_clear` never matched anything.

**New design primitives (`web/src/index.css`):** `.intel-rise` (fade+slide entrance), `.intel-live-dot` (pulse ring), `.intel-bar-fill` (width transition), `.intel-num` (tabular-nums), `.intel-scroll` (thin scrollbar) — deliberately restrained, reusing existing status/primary color tokens rather than introducing a new palette.

**New components:**
- `web/src/components/intel/EtaChip.tsx` — compact HUD/fleet-card chip (P50 + P10–P90 band, slip indicator); now has a `compact` variant for the fleet grid.
- `web/src/components/intel/EtaFactorBars.tsx` — SHAP-style diverging horizontal bars centered on a zero line (green = saves time, orange = adds time), staggered entrance animation.
- `web/src/components/intel/EtaCard.tsx` — full Idle Hub ETA card (remaining P50/band, progress bar, factor bars, model version).
- `web/src/components/intel/IdleAttributionCard.tsx` — self-polling (15s) stacked bar + legend over the 5 idle causes, deviation-flag warning banner. **Bug found and fixed:** an initial version built Tailwind classes as `` `${x}/15` `` template strings, which Tailwind's JIT scanner cannot see (it only scans literal source strings) and would have silently rendered unstyled; fixed by spelling out a static `badge` class per cause.
- `web/src/components/intel/AnomalyBadge.tsx` — compact/expanded anomaly indicator with top-3 SHAP-style driver chips.
- `web/src/components/incidents/IncidentList.tsx` — polls `GET /incidents?site_id=` every 10s (there is no site-wide WebSocket in this system — only per-machine `/ws/stream/{id}` — so polling is the correct mechanism here, not a workaround).
- `web/src/components/incidents/IncidentDetail.tsx` — status pill, Ack/Close(note) actions (SUPERVISOR/ADMIN only, 409s surfaced), explanation block with a source badge (`Groq (grounded)` vs `Deterministic fallback` + reason), probable causes with evidence chips linking to timeline events, recommended actions and lesson with knowledge chips that fetch `/knowledge/chunks/{id}` on demand and expand inline on click, and an ordered timeline.
- `web/src/views/IncidentPage.tsx` + route `/supervisor/incidents/:incidentId` (SUPERVISOR and ADMIN).
- `web/src/lib/types.ts` (TS mirrors of the intelligence + incident contracts) and `web/src/lib/format.ts` (`fmtMinutes`, `fmtSignedMinutes`, `fmtPercent`, `fmtSeconds`, `titleCase`, `relativeTime`).

**Rewired existing views:** `MachineCard.tsx` (now uses `useMachineStream` + `EtaChip`/`AnomalyBadge` + open-incident link), `Hud.tsx` (fixed G6 nav, real `EtaChip`, fixed `envelope.speed_cap` → `speed_cap_kmh` — another latent bug from before this batch), `IdleHub.tsx` (fixed G6 back-nav, real `EtaCard` + `IdleAttributionCard`), `Supervisor.tsx` (added `IncidentList` panel), `Admin.tsx` (added a live Worker Status panel from `/admin/workers`, polled every 10s), `App.tsx` (incident detail route).

**Two more real bugs found and fixed while manually verifying against the live stack (neither is scoped to "frontend intelligence integration" but both blocked reaching any authenticated view at all, so both are fixed here rather than filed away):**
1. **No CORS middleware on the backend at all** (`backend/app/main.py`) — every request from the Vite dev server (port 5173) to the API (port 8000) failed at the preflight `OPTIONS` step with a 405, so *no* frontend request of any kind could ever succeed against a separately-run API in local dev. Added `CORSMiddleware` with a new `settings.cors_origins` (defaults to the Vite dev origins).
2. **`App.tsx`'s `/` catch-all rendered a bare `<ProtectedRoute />` with no nested route** — Login navigated to `/` after signing in and landed on a blank page for every role, since `Outlet` had nothing to render. Replaced with a `RoleHome` component that redirects to the correct role's home route.

**Checks run:**
```
npm run build   # tsc -b && vite build — clean
npm run lint    # oxlint — 0 errors, only pre-existing-pattern warnings (react-hooks/set-state-in-effect etc., same classes already present in AuthContext.tsx before this batch)
```

**Manual verification against the real stack** (docker db+redis, `uvicorn backend.app.main:app`, `npm run dev`, browser pane):
- Logged in as `supervisor`/`demo123` → Fleet Supervisor renders live `MachineCard`s with working WS connection, compact `EtaChip`, and an "N open" incident link on the machine with a real open incident.
- Clicked into `IncidentDetail` for a real `LDR001` `HEALTH_THRESHOLD` incident (fallback explanation, `NO_API_KEY`): probable causes, recommended actions, and lesson rendered with real evidence/knowledge chips; clicking a knowledge chip fetched and expanded the real chunk text (`adverse-weather-operation` doc) inline.
- **Ack flow:** clicked Acknowledge → status pill flipped `OPEN → ACKNOWLEDGED`, a `STATUS`-kind timeline entry appeared ("Acknowledged by supervisor"), Ack button correctly disappeared.
- **Close flow:** opened the close-note form, "Confirm close" correctly stayed disabled until text was entered, submitted a note → status flipped to `CLOSED`, both action buttons correctly disappeared (`canManage && status !== 'CLOSED'` gate), timeline updated, and `IncidentList` on the Supervisor page reflected `Closed` immediately on the next poll with the open-incident link removed from the fleet card.
- Logged in as `admin`/`demo123` → Worker Status panel correctly showed `warm`/`cold` with real (if stale, from an earlier session) heartbeats and `hot`/`correlator` as "No heartbeat" (accurate — those two workers don't publish `worker:status` in the current backend).
- Zero new console errors at any point (checked after every action).

**Known limitations / deferred:**
- The operator HUD → Idle Hub live-telemetry transition (30-tick dwell) was **not** manually driven end-to-end in this batch — the demo DB already had persisted sequence state for the seeded machines from earlier sessions, and re-running the simulator against it produced `409 Sequence out of order` without a state reset. The component wiring (uses `uiMode`, not the old broken `currentState` check) was fixed and code-reviewed, and both `EtaCard`/`IdleAttributionCard` were verified against real backend data via the Idle Hub route's props — but the live tick-by-tick transition itself is more appropriately Batch 3J's job (full end-to-end verification), which can reset/seed a clean machine for it.
- `MachineCard`'s open-incident link points at `openIncidents[0]` (first only) when a machine has more than one open incident — acceptable for the fleet-grid glance view; the full list is one click away via `IncidentDetail`... via `IncidentList`, not yet a dedicated "all incidents for this machine" view.
- No automated frontend test runner exists in this project (confirmed before starting; the plan explicitly said not to add one without approval) — verification here is `tsc`/`oxlint` + the manual pass above, not automated tests.

**Next:** Batch 3J (Full end-to-end verification), awaiting approval.

---

## Stage 3 / Batch 3J: Full end-to-end verification  (2026-09-24)

**Status:** ✅ verified

**Goal:** prove the full chain Simulator → Hot → Warm → Event → Correlator → Incident → Cold → LLM/fallback → Frontend, including restarts and failure isolation, driven in-process rather than reasoned about component-by-component.

**New files:**
- `simulator/scenarios/demo_intel.yaml` — extends the original `demo.yaml` (RAIN/MUDDY + seatbelt + proximity) with a truck-absent SITE idle period, a truck-present OPERATOR idle period, and a held high-`fuel_lph` window for the anomaly detector.
- `tests/e2e/conftest.py` + `tests/e2e/test_pipeline.py` — one real Postgres+Redis test driving signed telemetry through `/ingest/telemetry` (real HMAC + freshness + Lua sequence checks), then `WarmProcessor`/`Correlator.handle_event`/`ColdWorker.process` in-process (matching how the Batch 3D/3E/3H integration suites already exercise each individually) on the SAME telemetry the HTTP layer actually published — not synthetic events. Asserts: window persisted → hot SEATBELT_VIOLATION (CRITICAL) + PROXIMITY_BREACH (WARNING) → incident opened and extended → both in one timeline → cold worker explanation persisted (`groq_api_key` cleared, so the real deterministic-fallback code path, not a mock — the mocked/real-Groq-success paths already have dedicated coverage from Batch 3G/3H) → `GET /incidents/{id}` and `GET /machines/{id}/snapshot` both reflect it.
- `tests/e2e/test_restart.py` — three tests, one per worker, each killing a live processor mid-scenario and continuing with a fresh instance (a real restart's shape: unacked/redelivered messages, no in-memory state carried over): warm (deterministic `window_id` + `ON CONFLICT DO NOTHING` → no duplicate window row), correlator (redelivering an already-linked event → `SKIP_DUPLICATE`, not a second incident), cold (`packet_hash` cache → `CACHED`, not a second explanation row).
- `scripts/e2e_smoke.sh` — brings up the API + all 4 background workers + the `demo_intel` simulator scenario for a configurable duration, then prints `/admin/workers` and real row counts. A human-facing smoke check, not part of the pytest suite.

**Files modified:**
- `Makefile` — `demo-intel`, `test-unit`, `test-integration`, `test-e2e` targets.
- **24 existing test files** (`tests/unit/*.py`, `backend/tests/unit/*.py`, `ml/tests/*.py`, `core/copilot_core/tests/test_engines.py`, `tests/test_batch4.py`, `tests/test_batch5.py`) — added the `pytestmark = pytest.mark.unit` (or `.integration`) that `pytest.ini` had defined markers for since Batch 3A but that almost nothing actually used, so `make test-unit`/`test-integration`/`test-e2e` were silently no-ops before this batch. Mechanical, but caught **two real bugs in the process**: a scripted insertion pass split two files' multi-line `from x import (...)` statements in half (`tests/unit/test_ids.py`, `tests/unit/test_simulator.py`, `backend/tests/unit/test_correlator.py`), which `py_compile`'d clean on everything *except* those three — fixed by hand before running anything.

**Real bugs found and fixed while building the e2e test (not in the files above — pre-existing gaps the pipeline test exposed):**
1. `Correlator.handle_event()` does **not** itself XADD to `incidents:work` — that's a separate `correlator.publish(event, incident_id, action)` call the real consumer loop (`_process_message` in `correlator.py`) makes afterward. The first draft of `test_pipeline.py` assumed `handle_event` alone was enough and its "the cold worker got queued" assertion failed empty — fixed by mirroring `_process_message`'s exact two-step pattern in the test, which is also a useful confirmation that this two-step contract is real and intentional, not an oversight.
2. `incident_id` is deterministic (`contracts.ids.incident_id`, derived from `machine_id` + the triggering event's id) — a second test run within the same real Redis streams reproduced the exact same incident id as a prior run, so a naive `xrange()` over the whole (shared, cross-run) `telemetry`/`events`/`incidents:work` streams picked up stale entries from earlier manual/test sessions. Fixed by recording each stream's last entry id before the test acts and reading only newer entries — the streams are real infrastructure shared with manual demo runs, not a test-only fixture, so this is the correct fix rather than a workaround.
3. `tests/e2e/test_restart.py` imported a helper via `from tests.e2e.conftest import aligned_window_start` inside a test body — this passed every time `pytest tests/e2e/` ran alone (its own invocation happens to make `tests.e2e` resolve), but broke with `ModuleNotFoundError: No module named 'tests.e2e'` the first time it was collected alongside `tests/unit/`, `tests/integration/`, `backend/tests/`, etc. in one combined run — `tests/` has no `__init__.py` (matching `tests/unit/`/`tests/integration/`'s existing convention), so cross-package imports between test modules aren't reliable regardless of invocation. Only surfaced at this batch's true full-suite checkpoint, never caught by the isolated `tests/e2e/` runs used while writing the test. Fixed by inlining the three-line helper instead of importing it — conftest.py fixtures (`redis_client`, `clean_machine`) are unaffected since pytest discovers those by directory, not import.

**Tests executed (command + result):**
```
PYTHONPATH=. venv/bin/python -m pytest tests/e2e/ -v
4 passed in ~8s (run twice back-to-back to confirm no cross-run flakiness — both green)

PYTHONPATH=. venv/bin/python -m pytest tests/ core/copilot_core/tests/ ml/tests/ backend/tests/ -q
258 passed (final full-suite run, after the Stage 4A fixes below)
```

**Manual verification:**
- `npm run build` / `npm run lint` — clean (frontend untouched by this batch beyond what 3I already covered).
- Did **not** run `scripts/e2e_smoke.sh` for a full 3-minute wall-clock demo pass in this session (the automated `tests/e2e/` suite above already exercises the identical chain deterministically and faster) — the script is provided and manually runnable (`./scripts/e2e_smoke.sh 180`) for a human-facing demo/sanity check, e.g. before a live presentation.
- Hot-path latency spot check against Batch 3.0's baseline was not re-measured numerically in this session; hot.py's per-frame logic is unchanged by this batch (3J added no hot-path code — Stage 4 did, see below).

**Known limitations / deviations:**
- `test_pipeline.py`'s cold-worker step uses the deterministic-fallback path (`groq_api_key` cleared), not a mocked-success Groq response — a deliberate scope choice, documented in the test's own module docstring, since the mocked/real-API paths already have dedicated coverage elsewhere and this batch's job is proving pipeline *wiring*, not re-proving grounding.
- The marker pass (`pytest.mark.unit`/`.integration`) is now consistent across the suite, but `test-unit`/`test-integration` were never actually run as a gate in CI before this batch (there is no CI in this project) — this is a local convenience fix, not a new enforced gate.

**Next:** Stage 3 complete. Stage 4 (lessons, effectiveness tracker, instructor booking, shift handover) is a new phase — see below; the user explicitly scoped it down to 4A–4C only (lesson generation, IDLE_HUB delivery, lesson player), declining the full spec and declining to fake unimplemented pieces.

---

## Frontend visual redesign  (2026-09-24)

**Status:** ✅ verified

**What:** a full visual/layout redesign of the React frontend — persistent role-aware `Sidebar` + `AppLayout` wrapper (new), refreshed `index.css` design tokens, and a from-scratch pass over `Login`, `PreStart`, `Hud`, `IdleHub`, `Supervisor`, `Admin`, `IncidentPage`, `MachineCard`, `ThreeDVis`, and the `intel`/`incidents` components — driven by a design brief the user requested (a Stitch/Antigravity prompt grounded in this codebase's actual data fields and routes) and executed by an external tool against an `implementation_plan.md` reviewed beforehand. State management, API contracts, routing guards, and WS logic were explicitly out of scope for that plan and stayed untouched. This entry only covers what I verified and fixed against the real running stack, not the design work itself.

**Two real bugs found and fixed while verifying it:**
1. **`Sidebar.tsx`'s `/supervisor/incidents` nav item was permanently stuck "active."** Traced to React Router v7's `NavLink`: passing a plain string `className` does **not** replace the router's own active-detection — it still auto-appends `"active"` based on `to`-prefix matching, and only the *function* form of `className` bypasses that entirely. Both supervisor nav items point at `/supervisor` (there's no standalone incidents route — incidents live inline on that page), so the string form was always "active" regardless of the custom condition written inside it. Fixed by using the function form and computing the highlight from `useLocation()` (which was also missing — the original code referenced a bare, non-reactive `location` global).
2. **Any hard reload or direct URL open on a protected route bounced straight to `/login`, even with a valid session.** `AuthContext`'s `user` was derived from the stored JWT only inside a `useEffect`, so `token` was truthy but `user` was still `null` on the very first render — `ProtectedRoute` saw that and redirected before the effect ever ran. Fixed by deriving `user` synchronously in the `useState` initializer instead of only reactively.

**Manual verification against the real stack:** logged in as all three roles; supervisor fleet grid + incident detail (ack/close) + sidebar highlighting across a hard reload; admin worker-status panel + a genuine `Chain Compromised` audit result (real tampered state from an earlier session, not a bug); operator pre-start → HUD. Zero console errors at any point. `npm run build` / `npm run lint` clean throughout (same pre-existing warning classes as before, nothing new).

**Known limitation, not fixed:** the HUD's floating side cards (Machine Overview / Task ETA) overlap the 3D visualization below ~768px viewport width — within the plan's own stated support matrix (1280/1024/768px) but not exercised by the redesign's own verification pass. Left as-is; flagged to the user rather than silently patched, since it's cosmetic and not this session's scope.

---

## Stage 4 / Batch 4A-4C: Lesson generation + IDLE_HUB delivery + lesson player  (2026-09-24)

**Status:** ✅ verified

**Scope note:** the user's original request was the full Stage 4 spec (lesson generation, delivery, player, replay quiz, effectiveness tracking, Training Hub, instructor booking, shift handover). When time pressure came up, the request shifted to "make something that looks like it works, it doesn't have to be implemented all the way — it can't be checked." That was declined: this project's whole "Data Rule" discipline (never fabricate backend data, honest empty/unavailable states everywhere) is worth more than one fake-looking feature, and a fake feature is also just a bug waiting to be found by anyone who clicks it. The user was offered a real, scoped-down alternative instead and chose **4A–4C only, using Groq (not Gemini, matching the rest of this codebase)**. 4D–4H (quiz, effectiveness tracking, Training Hub, instructor booking, shift handover) are explicitly out of scope for this batch — see "Known limitations" below for exactly how that's represented in the UI (never as a fake working feature).

**New tables:** `lessons` (migration `a504e0bd6bc3_stage4_lessons`) — one row per incident (deterministic id via `contracts.ids.lesson_id`, unique `incident_id` FK), `title`/`short_tip`/`explanation`/`knowledge_refs` (grounded), `source` (GROQ|FALLBACK), `status`, `fallback_reason`, `generated_at`/`delivered_at`/`read_at`.

**New endpoints:** `GET /lessons` (operator's own, newest first), `GET /lessons/{id}` (RBAC: operator must own it, supervisor/admin unrestricted; first fetch stamps `read_at`).

**New UiPush type used:** `lesson_ready` (already reserved in the `UiPushType` enum since an earlier batch, unused until now) — full lesson content pushed directly, no second round trip needed on delivery.

### 4A — Lesson generation

Mirrors the Batch 3G/3H incident-explanation pipeline almost exactly, reusing rather than duplicating: the SAME Incident Packet (`genai/packet.py`, same `allowed_event_ids`/`allowed_chunk_ids`), the SAME Groq client plumbing (`client.py`'s `_structured_completion` was factored out of `generate_explanation` so `generate_lesson_content` shares it rather than copy-pasting the retry/timeout/error handling), and the SAME retry-once-then-deterministic-fallback policy. New pieces: `genai/lesson_schemas.py` (`LessonLLM`: title/short_tip/explanation/knowledge_refs), `genai/lesson_prompts.py`, `genai/lesson_validator.py` (reuses `validator.py`'s smuggled-ref/forbidden-phrase checks directly — same rules, can't drift apart), `genai/lesson_fallback.py` (deterministic, category-keyed tip/explanation text, always grounded to a real chunk_id).

Hooked into `ColdWorker.process()` right after the explanation is persisted (`_maybe_generate_lesson`): skipped when the incident has no assigned operator, skipped if a lesson already exists for that incident (deterministic id, checked before calling the LLM at all), and wrapped so any lesson-generation failure is logged and swallowed — **a lesson bug must never break incident explanation**, verified by a dedicated test that patches `_get_lesson` to raise and confirms the explanation still persists.

### 4B — IDLE_HUB delivery

Hooked into `hot.py`'s existing per-frame `ui_mode` computation: a new `MachineHotState.last_ui_mode` field detects the HUD→IDLE_HUB *edge* (not every frame spent idle), and only on that edge does `_maybe_deliver_lesson(operator_id, machine_id, ts)` run — the operator's oldest undelivered lesson (`delivered_at IS NULL`), stamped delivered and pushed in one step. Deliberately uses its **own** short-lived DB session rather than the frame-processing session already in scope, so a lesson-delivery bug can never affect hot-path event/state persistence, and the write isn't left stranded if the frame-processing session's batch never happens to call `flush_buffers`. Frontend does **not** compute IDLE_HUB itself (per the spec's own constraint) — it only reacts to `ui_mode` the backend already sends, unchanged from Batch 3I.

### 4C — Lesson player

`web/src/components/lessons/LessonPlayer.tsx`, wired into `IdleHub.tsx` in place of the old "Aspirational" placeholder card. Prefers the live `lesson_ready` WS push (full content, zero extra round trip); falls back to `GET /lessons?limit=1` + `GET /lessons/{id}` so a page reload during IDLE_HUB doesn't lose a lesson that already arrived server-side (`delivered_at` is the real source of truth, the WS push is just the fast path). Shows title, highlighted tip, explanation, real knowledge-citation chips, a source badge ("Grounded" vs "Deterministic"), and an honestly-**disabled** "Start Quiz · Coming soon" button — never a fake working quiz.

**A real regression found and fixed at the full-suite checkpoint (not caught while iterating — this is exactly why the checkpoint step in the implementation discipline matters):** adding `lessons.incident_id` as a foreign key broke three existing integration test cleanup fixtures (`test_cold_worker.py`, `test_correlator_worker.py`, `tests/e2e/conftest.py`) that `DELETE FROM incidents` directly — any incident processed by the now-lesson-generating `ColdWorker.process()` during those tests left a `lessons` row behind, and Postgres correctly refused to delete the incident out from under it (`ForeignKeyViolationError`). 7 failed + 13 errored on the first full-suite run after 4A landed. Fixed by extending each fixture's cleanup to delete from `lessons` first too, matching this project's existing convention (no `ON DELETE CASCADE` anywhere in this schema — every table with a child row cleans up explicitly, same pattern `IncidentExplanationRow`/`IncidentTimeline`/`IncidentEvent` already used).

**Tests executed (command + result):**
```
PYTHONPATH=. venv/bin/python -m pytest backend/tests/unit/test_lesson_fallback.py backend/tests/unit/test_lesson_validator.py -v
14 passed

PYTHONPATH=. venv/bin/python -m pytest tests/integration/test_lessons.py tests/integration/test_lesson_delivery.py tests/integration/test_lesson_api.py -v
11 passed (generation+fallback+dedup+failure-isolation, delivery-exactly-once on the real IDLE_HUB transition, RBAC+read-marking via TestClient)

PYTHONPATH=. venv/bin/python -m pytest tests/ core/copilot_core/tests/ ml/tests/ backend/tests/ -q
258 passed — full suite, after the FK-cleanup fix above
```

**Manual verification (real stack, not just tests):** seeded a real lesson row for the real demo operator against the real running API + hot worker; logged in as `operator`, completed the real Pre-Start checklist for EXC001, confirmed the network log showed `GET /lessons?limit=1` → 200 → `GET /lessons/{id}` → 200 (the REST-fallback path, proven live); navigated to `/operator/idle` and visually confirmed the lesson player renders the real title ("Seatbelt Compliance"), the real highlighted tip, the real full explanation paragraph, the real `seatbelt-safety#overview` citation chip, the correct "Deterministic" source badge (this lesson used the `NO_API_KEY` fallback path), and the disabled "Start Quiz · Coming soon" button. Zero console errors. Cleaned up the seeded rows afterward.

**Known limitations / explicitly out of scope (labelled honestly in the UI, not faked):**
- **4D (replay quiz):** the player's "Start Quiz" button is real, visible, and permanently disabled with a tooltip — there is no quiz backend at all.
- **4E (effectiveness tracking), 4G (instructor booking), 4H (shift handover):** not started — no tables, no endpoints, no UI surface referencing them anywhere.
- **4F (Training Hub):** no standalone page exists; `GET /lessons` (list) is implemented and RBAC-correct, so a Training Hub could be built on top of it later without backend changes, but nothing renders it yet.
- Lesson generation is unconditional for every incident with an operator_id — there's no per-operator rate limiting or "don't re-teach the same lesson twice in a week" logic; every distinct incident gets its own lesson.

**Next:** awaiting direction — 4D onward, or a different priority. Not starting more Stage 4 scope without explicit approval, per the user's own instruction.
