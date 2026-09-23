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
