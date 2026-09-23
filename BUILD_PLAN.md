# CAT Co-Pilot — 24h MVP Build Plan

Companion to `ARCHITECTURE.md`. Aim high in the architecture, ship an MVP in the implementation. Every stage ends in a **checkpoint**: a demoable, merged state on `main`.

---

## 1. Scope tiers

| Tier | Items |
|---|---|
| **P0 (demo dies without it)** | Simulator + scenario injector · signed ingest → Redis Streams → hot worker · state classifier · condition-adaptive safety (seatbelt, proximity, speed) · arbitrator · risk trajectory · HUD ↔ Idle Hub · task dashboard + pre-start checklist · ETA (P50 + band + SHAP factors) · idle attribution + contextual baseline · incidents + timeline · Gemini incident explanation (with fallback) · micro-lesson in idle window · JWT RBAC (operator, supervisor, admin) · hash-chained audit |
| **P1 (differentiators, build if on schedule)** | Isolation Forest + SHAP drivers · incident-replay quiz · effectiveness tracker · instructor booking · fleet manager page · shift handover · fatigue indicator · "why?" chat on cards |
| **P2 (pitch only / stretch)** | Voice push-to-talk (Web Speech API) · PWA offline · Timescale hypertable · pgvector · Prometheus |

**Cut-list if behind at H14 (in order):** fleet page → shift handover → booking → IF SHAP (use robust-z drivers) → effectiveness tracker (show a static computed delta) → IF entirely (keep contextual baseline).

---

## 2. Team roles

| Member | Owns |
|---|---|
| **M1 Frontend** | All React views, WS client, design system, demo-director UI |
| **M2 Data/ML** | Synthetic generator (history + live), scenario YAMLs, ETA models, baseline/attribution, Isolation Forest, SHAP, metrics |
| **M3 Streaming/Core** | Docker Compose, ingest + HMAC, Redis Streams sharding, hot worker, `copilot_core` (state, safety envelope, arbitrator, risk, health), WS gateway, golden tests |
| **M4 Platform/AI/Pitch** | DB schema + Alembic, auth/RBAC, audit chain, incidents/correlator APIs, Gemini layer, knowledge base, training/lessons APIs; owns the pitch deck from H18 |

Sleep: staggered 2 × 90 min per person (M1+M2 at H11–12.5, M3+M4 at H12.5–14). Nobody sleeps during H18–H22.

---

## 3. Repo structure

```
cat-copilot/
├── docker-compose.yml          .env.example   Makefile
├── contracts/                  # SINGLE SOURCE OF TRUTH — frozen at H1.5
│   ├── events.py               # Pydantic: TelemetryFrame, ContextFrame, Event, Incident, UiPush
│   └── openapi.md              # endpoint list + WS message types
├── core/copilot_core/          # pure, IO-free, deterministic (edge-portable)
│   ├── state.py  envelope.py  safety.py  arbitrator.py  risk.py  health.py
│   └── tests/                  # unit + golden scenario replays
├── backend/app/
│   ├── main.py  deps.py(auth)  config.py
│   ├── api/  auth.py tasks.py machines.py incidents.py training.py admin.py audit.py ws.py ingest.py
│   ├── db/   models.py  session.py  migrations/
│   ├── services/ audit_chain.py correlator.py attribution.py eta.py anomaly.py
│   ├── genai/ client.py tools.py prompts.py validator.py fallback.py
│   ├── knowledge/ *.md  retriever.py
│   └── workers/ hot.py warm.py cold.py
├── ml/
│   ├── generate_history.py  train_eta.py  train_anomaly.py  baselines.py  evaluate.py
│   └── artifacts/  eta_p50.json eta_q.json iforest.joblib meta.json
├── simulator/  sim.py  scenarios/*.yaml
└── web/ (Vite React TS)
    └── src/ views/{PreStart,Hud,IdleHub,Supervisor,Fleet,Admin} components/ store/ ws/
```

---

## 4. Stages

### Stage 0 — Contracts & scaffold (H0 → H1.5) · everyone

- [ ] Read the brief together (10 min). Agree on the P0 list. No debate after this.
- [ ] M3: repo, compose (postgres, redis, api, workers, simulator, web), `make up`, `.env.example`
- [ ] M4: `contracts/events.py` (Pydantic) + DB schema draft + endpoint list (`openapi.md`)
- [ ] M2: generator field list + distributions table; agree which fields the demo needs
- [ ] M1: Vite + TS + Tailwind + shadcn init, routing skeleton, `ws/mockFeed.ts` generating fake UiPush
- [ ] Freeze UiPush message types:
  `state_change | alert | alert_clear | risk | envelope | eta | idle_attribution | lesson_ready | incident`

**Checkpoint 0:** `make up` boots all containers; contracts merged; mock HUD renders fake data.

---

### Stage 1 — Data & spine (H1.5 → H5)

**M2 — generator**
- [ ] Entities: 1 org, 1 region, 2 sites, 6 machines (4 EXC, 2 loaders), 8 operators (skill mix), 5 task types
- [ ] History: 60 days → ~5,000 task rows plus window aggregates (~50k)
- [ ] Correlations (calibrate ranges to the provided sample rows):
  - `actual = base(type) × weather_m × ground_m × skill_m × (1+0.03·age) × lognormal(0, 0.08) + queue_delay`
  - weather_m: sunny 1.0, cloudy 1.03, windy 1.1, rain 1.18; skill_m: expert 0.93, intermediate 1.05, beginner 1.3
  - idle = context baseline + hauler-queue waits (site) + operator excess (heavy-tailed for ~15 % of operators)
  - fuel_rate ∝ rpm × load; seatbelt unfastened p higher for beginners and short repositioning moves
  - planner_estimate = base(type) × naive skill factor (this is the baseline we must beat)
  - ~3 % labelled injected anomalies (high fuel/cycle, erratic cycle times, hot engine)
- [ ] Live sim: 1 Hz frames, time compression flag (`--speed 10`), HMAC signing, seeded
- [ ] Scenario YAML DSL:
  ```yaml
  - at: 30s   set: {machine: EXC001, seatbelt: UNFASTENED}
  - at: 40s   repeat: {every: 6s, times: 4, set: {proximity: {zone: ORANGE, distance_m: 5.5}}}
  - at: 20s   context: {site: SITE-A, weather: RAIN, rainfall_mm_h: 12, ground: MUDDY}
  ```

**M3 — spine**
- [ ] `POST /ingest/telemetry` + `/ingest/context`: verify HMAC, seq monotonic, ts window, schema → `XADD telemetry:{shard}`
- [ ] Hot worker skeleton: consumer group per shard, per-machine state in Redis hash, `XACK` after process, `XAUTOCLAIM` on start
- [ ] WS gateway: `/ws/machine/{id}`, `/ws/site/{id}` subscribed to Redis pub/sub `ui:*`
- [ ] Batch persister: telemetry → Postgres every 1 s

**M4 — platform**
- [ ] SQLAlchemy models + Alembic migration + seed script (org hierarchy, users, tasks for today)
- [ ] Auth: `/auth/login` → JWT `{sub, role, site_ids, machine_ids}`; `require(roles, scope)` dependency
- [ ] Audit chain service: `append(actor, action, target, payload)` + `GET /audit/verify`
- [ ] Task endpoints: `GET /me/tasks`, `GET /sites/{id}/tasks`

**M1 — UI**
- [ ] Login, role-based routing
- [ ] Pre-start checklist → Start shift
- [ ] HUD layout against the mock feed: 3 status bars, envelope badge, cycle counter, ETA chip, alert zone (max 3)

**Checkpoint 1 (H5):** simulator → ingest → Redis → hot worker logs frames; login works; HUD renders from mock.

---

### Stage 2 — Hot path live: the walking skeleton (H5 → H9)

**M3 — `copilot_core`** (pure functions, event-time only)
- [ ] `state.py`: OFF/IDLE/TRAVEL/WORKING with dwell + asymmetric hysteresis; UI mode HUD/IDLE_HUB (30 s dwell)
- [ ] `envelope.py`: base params × max-of condition multipliers → `Envelope{red_r, orange_r, speed_cap, notes}`
- [ ] `safety.py`: seatbelt (with ingress grace), proximity vs envelope, overspeed vs condition cap
- [ ] `health.py`: temp/hyd thresholds + temp slope
- [ ] `arbitrator.py`: first-occurrence emit, CRITICAL latch, 30 s aggregation → PERSISTENT at severity+1, ack TTL, flood guard, suppressed-audio counter
- [ ] `risk.py`: decay + weighted events × condition multiplier, hysteresis levels
- [ ] Golden test: `scenarios/demo_main.yaml` replay → assert exact alert sequence and zero CRITICAL suppressed
- [ ] Wire into hot worker → publish UiPush, emit Events to `events` stream

**M1**
- [ ] Swap mock → real WS; HUD ↔ Idle Hub transition animation; audio chime (rate-limited per arbitrator flag); ack button (56 px+)
- [ ] Envelope badge ("Rain · zones +30 % · cap 4 km/h"), risk bar with level colour + icon + text

**M2**
- [ ] `baselines.py`: EWMA expected idle ratio with hierarchical backoff; robust z
- [ ] `train_eta.py`: P50 (`reg:absoluteerror`) + quantile (`reg:quantileerror`, α = [0.1, 0.9]); save + `meta.json` (features, version, MAE)

**M4**
- [ ] Knowledge base: write ~10 markdown docs (paraphrased, headed sections, tags front-matter)
- [ ] `retriever.py` BM25 over heading chunks, tag filter
- [ ] Gemini client: tool definitions, manual tool loop, RBAC-scoped executors, JSON schema output, 8 s timeout, cache

**Checkpoint 2 (H9) — non-negotiable:** run `demo_main.yaml` → a real seatbelt + proximity sequence appears on the HUD, consolidates into one PERSISTENT alert, risk climbs and then decays after the fix, machine stops → Idle Hub opens. Record a 30 s screen capture as insurance.

---

### Stage 3 — Warm path: ML & attribution (H9 → H14)

**M2**
- [ ] ETA service: predict P50/P10/P90 at task start; SHAP with task-type background → grouped factors in minutes; live blend with observed cycle rate; emit `ETA_SLIP`
- [ ] Attribution: segment idle (PLANNED > MACHINE > WEATHER > SITE > OPERATOR) per window; operator deviation flag
- [ ] (P1) Isolation Forest on window aggregates; percentile-calibrated score; SHAP top-3 drivers; fallback robust-z
- [ ] `evaluate.py`: ETA MAE vs planner estimate, P10–P90 coverage, IF precision/recall → `metrics.json` (feeds the pitch)

**M3**
- [ ] Warm worker: window aggregator (30 s demo windows), calls ETA/attribution/anomaly, emits events
- [ ] Correlator: 5 min per-machine window → open/extend incident, ordered timeline, severity escalation
- [ ] `GET /machines/{id}/snapshot` (last state for WS reconnect)

**M4**
- [ ] Incidents API: list/get/ack/close (audited), timeline endpoint
- [ ] Cold worker: on incident → Gemini explanation → grounding validator → store; fallback template path tested with the API key unset

**M1**
- [ ] Idle Hub: task list, ETA card (P50, band, factor waterfall), idle attribution donut/bar (site vs operator minutes), pending lesson card
- [ ] Supervisor view: machine cards sorted by risk, incident list

**Checkpoint 3 (H14):** Idle Hub shows real ETA factors and attribution; an incident is auto-created with a Gemini (or fallback) explanation visible to the supervisor.

---

### Stage 4 — Cold path, training loop, roles (H14 → H18)

**M4**
- [ ] Lesson generator: incident → retrieve chunks → 30 s tip + (P1) 3-question replay quiz, all citing `chunk_id`s → deliver on next `IDLE_HUB`
- [ ] (P1) Effectiveness tracker: target metric N windows before/after lesson → `lesson_outcomes`
- [ ] (P1) Instructor booking: slots, request, supervisor approve (audited)
- [ ] (P1) Shift handover summary endpoint

**M1**
- [ ] Lesson player + quiz; training hub tab (video links, booking)
- [ ] Incident detail: timeline ("What changed?") + Gemini explanation with evidence chips + citations
- [ ] Admin: demo director (scenario buttons, weather toggles, speed slider), audit verify button (✓ / ✗), threshold editor
- [ ] (P1) Fleet page: 4 KPI tiles + per-site table

**M3**
- [ ] Hot-path latency metric (ingest ts → publish ts), p95 on admin page
- [ ] Hardening: WS auto-reconnect + snapshot, worker restart test (kill -9 mid-scenario → recovers)

**M2**
- [ ] (P1) Fatigue indicator (continuous op time, ack latency trend)
- [ ] Tune demo scenario so the numbers land cleanly (visible idle 2.4× deviation, anomaly fires once)

**Checkpoint 4 (H18) — feature complete:** the full demo scenario runs end-to-end on one laptop with `make demo`.

---

### Stage 5 — Integration & hardening (H18 → H21)

- [ ] Full run ×3 with clean DB (`make reset && make demo`); fix only blockers
- [ ] Pre-warm the Gemini cache for demo incidents; verify the offline fallback path
- [ ] Audit tamper demo: `make tamper` edits one row → verify shows ✗ at that entry
- [ ] RBAC demo: operator token → 403 on another site's machine
- [ ] Freeze thresholds; seed fixed
- [ ] **Code freeze H20.** After this, bug fixes only, with a second person reviewing.
- [ ] H20–21: record the full backup demo video (1080p, voiceover optional)

M4 switches to the deck at H18: problem → thesis → architecture (3 paths) → live demo → numbers → roadmap.

---

### Stage 6 — Pitch & submission (H21 → H24)

- [ ] Rehearse demo ×3 with a timer; assign driver (M1) and narrator (M4); M3 on standby for recovery
- [ ] README: one-command run, architecture diagram, screenshots, metrics, "what's synthetic"
- [ ] Submit repo + video + deck by H23.5 (30 min buffer)

---

## 5. Demo script (≈3:30)

| Time | Scene | Action (demo director) | What judges see |
|---|---|---|---|
| 0:00 | Morning | Operator logs in, pre-start checklist, today's tasks | Daily experience starts before the engine does |
| 0:25 | Focused work | Machine WORKING | Minimal HUD: green bars, cycle counter, ETA 47 min [41–56] |
| 0:45 | Conditions change | Toggle RAIN + MUDDY | Envelope badge updates live: zones +30 %, speed cap 4 km/h; ETA re-factors (+6 min weather/ground) |
| 1:10 | Converging hazard | Seatbelt unfastened + 4 ORANGE detections in 30 s | First alert immediate; repeats merge into one PERSISTENT nudge; risk LOW→ELEVATED→HIGH; counter "4 detections, 3 chimes consolidated, 0 critical suppressed" |
| 1:45 | Recovery | Seatbelt fastened, speed down | Risk decays HIGH→ELEVATED→LOW |
| 2:05 | Idle window | Machine stops, no truck | Idle Hub: 41 min SITE (hauler queue) vs 14 min OPERATOR; micro-lesson + replay quiz from *this* incident, with citations |
| 2:40 | Supervisor | Switch role | Incident timeline, Gemini root-cause with evidence chips, assign training, lesson effectiveness "breaches −62 %" |
| 3:05 | Trust | Admin: audit verify ✓ → tamper → ✗ | Safety decisions are auditable and tamper-evident |
| 3:20 | Close | Architecture slide | "Telemetry → context → attention → action → learning" |

---

## 6. Judge Q&A prep

| Q | A |
|---|---|
| Why suppress alarms? | We don't. First occurrence and CRITICAL are always shown and latched; only repeated audio is consolidated, following ISA-18.2 flood guidance. Everything consolidated is logged. |
| Doesn't Cat already do this (VisionLink/Detect)? | They produce the signals. We are the in-cab arbitration and learning layer on top, and we feed attributed metrics back. |
| Why Isolation Forest? | Unlabelled, multivariate, cheap, tree-based so SHAP works; validated on injected anomalies. |
| Synthetic data? | Documented physical correlations; we beat the brief's own planner estimate; the pipeline is data-agnostic. |
| LLM hallucination? | It only sees structured incidents via read-only, role-scoped tools; outputs are schema-validated with an evidence and citation check; template fallback. It never touches safety. |
| Scale to 10k machines? | Sharded streams with per-machine ordering, stateless workers, batch writes, Timescale; contracts are Kafka-ready. The hot path can move to an edge gateway. |
| Privacy/surveillance? | Attribution protects operators from blame for site delays; operator sees own data first; framing is coaching. |
| Why not Kafka/K8s/agents? | Operational cost without benefit at prototype scale; bolt-on path is documented. |

---

## 7. Commands (target)

```bash
make up          # docker compose up -d --build
make seed        # migrations + org/users/tasks + knowledge chunks
make train       # generate history, train ETA + IF, write metrics.json
make demo        # start simulator with scenarios/demo_main.yaml at --speed 10
make test        # pytest core golden scenarios
make reset       # wipe db/redis, reseed
make tamper      # corrupt one audit row for the verify demo
```

`.env`: `GEMINI_API_KEY, GEMINI_MODEL, JWT_SECRET, MACHINE_HMAC_KEYS, DATABASE_URL, REDIS_URL, SIM_SPEED`

---

## 8. Pre-demo checklist

- [ ] Fresh `make reset && make seed && make demo` works on the demo laptop, offline except Gemini
- [ ] Gemini cache warm; fallback verified with the key removed
- [ ] Browser zoom 100 %, tablet viewport, dark mode, sound on
- [ ] Backup video on local disk and cloud
- [ ] Deck numbers match `metrics.json`
- [ ] All Caterpillar product claims on slides verified against their public pages
