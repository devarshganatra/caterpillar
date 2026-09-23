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
PYTHONPATH=. python -m backend.app.workers.hot_worker
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
