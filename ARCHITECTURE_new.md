# CAT Co-Pilot — Architecture & Design Rationale

> **Thesis:** The cab's scarce resource is not data, it is operator *attention*. CAT Co-Pilot is an attention-aware companion: it decides **what** the operator needs, **when** they can safely absorb it, and **how** to turn every near-miss into measurable skill improvement — with deterministic safety, explainable ML, and an LLM that explains but never decides.

---

## 0. Verdict on the two drafts

| Keep | From | Why |
|---|---|---|
| State-driven UI (Active HUD ↔ Idle Hub) | Draft 1 | Directly answers "companion that enhances daily experience"; nobody else will model cognitive load |
| Alert arbitration / anti-alarm-fatigue | Draft 1 | Real, documented industrial problem (ISA-18.2 alarm floods) |
| Site-vs-operator downtime attribution | Draft 1 | Makes "excessive idling" fair and defensible |
| Idle-window micro-learning | Draft 1 | Closes training loop without distraction |
| Event correlation → incident → Gemini | Draft 2 | LLM sees structured, validated context only |
| Org → Region → Site → Machine hierarchy | Draft 2 | Enterprise realism; row-level scoping |
| Audit log, incident timeline | Draft 2 | Cheap, high credibility |
| Redis Streams not Kafka; no microservices/LangChain/agents | Both | Correct tradeoffs |
| "Operator Intelligence", not "Monitoring" | Draft 2 | Ethics + union/privacy optics |

| Reject / fix | Issue |
|---|---|
| Draft 2 re-centres on a fleet platform | Brief is an **operator interface**. Operator UI is the hero; supervisor/fleet are supporting views |
| Isolation Forest "primary contributing signals" | IF has no native attribution → use SHAP TreeExplainer (supports `IsolationForest`), fallback robust-z |
| "Anomaly score 0.91" | `score_samples` is negative/unbounded → calibrate to 0–1 via training-set percentiles |
| "ETA confidence 84%" | XGBoost point regression has no confidence → quantile models (P10/P50/P90) |
| Payload mixes 1 Hz signals with shift aggregates (`idling_time_min: 55`) | Split into TelemetryFrame (1 Hz), ContextFrame (on change), WindowAggregate (computed) |
| Idle threshold 30 s vs 45 s | Single configurable dwell with hysteresis |
| "Working conditions" underused | Promote to a first-class **Condition-Adaptive Safety Envelope** |
| "Suppressing safety alarms" (as written) | Will get attacked. Arbitration must be **fail-safe**: consolidate repeats, never suppress first/critical |

---

## 1. Problem decoded

**Literal asks:** task dashboard · seatbelt/proximity/incident logging · *working conditions considered* · training hub (video/booking/simulation) · unusual behaviour (idling, unsafe patterns) · task-time prediction from history + environment.

**Implicit asks (the words judges wrote deliberately):** "intelligent **companion**", "operator's **daily experience**", "**end to end**", "**beyond just a tool**".

**What ~1,600 teams will submit:** telemetry dashboard + charts · LLM chatbot wrapper · one sklearn model per bullet · static threshold alerts. Differentiation must come from *orchestration, context and trust*, not from any single model.

---

## 2. Novelty pillars

| # | Pillar | What it is | Why it wins |
|---|---|---|---|
| 1 | **Attention Budgeting** | Machine-state classifier drives UI mode; hot-path alerts arbitrated per ISA-18.2 flood logic; coaching locked out while working | Treats cognitive load as an engineering constraint |
| 2 | **Condition-Adaptive Safety Envelope** | Weather, visibility, ground, heat, wind reshape proximity radii, speed caps, break prompts in real time | Explicit brief requirement nobody will model properly; highly visual in demo |
| 3 | **Fair Attribution** | Idle split into SITE / MACHINE / WEATHER / PLANNED / OPERATOR against contextual baselines with cold-start backoff | Coaching not surveillance; supervisors get actionable site bottlenecks |
| 4 | **Explainable-by-Construction ML** | SHAP-additive ETA in minutes, grouped by factor; quantile band; anomaly drivers | Every number defends itself |
| 5 | **Near-Miss → Lesson → Proof** | Each incident becomes a grounded micro-scenario delivered in the next idle window; target metric tracked before/after | Only team that *measures* training effectiveness |
| 6 | **Trust Layer** | Deterministic safety; LLM off critical path; RBAC-scoped read-only tools; grounding validator; HMAC-signed telemetry; hash-chained audit; graceful degradation | Enterprise and safety credibility |
| 7 | **Daily Bookends** | Pre-start checklist gates shift start; Gemini shift-handover brief for next operator | Covers the full workday, literally "daily experience" |

---

## 3. Requirement coverage

| Brief item | Implementation | Novel layer |
|---|---|---|
| Daily task dashboard | Tasks by shift, pre-start checklist, live ETA | Idle Hub expands only when safe |
| Seatbelt compliance | Rule: `seatbelt=UNFASTENED ∧ (speed>0.5 ∨ hyd_active)` → CRITICAL | Grace window for ingress/egress (engine on, stationary, <10 s) |
| Proximity hazards | 3-zone rule on `person_distance_m` | Condition-scaled radii, persistence consolidation |
| Incident logging | Correlator creates incidents from event clusters; timeline; audit | Gemini explanation with evidence refs |
| Working conditions | ContextFrame → envelope multipliers | Visible live envelope change |
| Training hub | Video library, instructor booking, **simulation = incident replay quiz** | Idle-window delivery + effectiveness tracking |
| Unusual behaviour | Contextual idle baseline + Isolation Forest + safety pattern rules | Attribution + SHAP drivers |
| Task time estimation | XGBoost P50 + quantile band, live blending with observed cycle rate | Factorized minutes via SHAP |

---

## 4. System architecture

### 4.1 Three latency paths

```
                         ┌──────────────────────────────────────────┐
 Simulator / Machine GW  │  HMAC-signed TelemetryFrame (1 Hz)       │
 (edge in future)        │  ContextFrame (on change)                │
                         └───────────────┬──────────────────────────┘
                                         ▼
                              FastAPI /ingest (verify sig, seq, schema)
                                         ▼
                     Redis Streams  telemetry:{shard}  (shard = hash(machine_id) % N)
                                         ▼
 ┌──────────────────── HOT PATH  (target ≤250 ms, no ML/LLM) ───────────────────────┐
 │ State Classifier → Safety Envelope Rules → Health Rules → Alert Arbitrator       │
 │           → Risk Trajectory update → PUBLISH ui:machine:{id}                     │
 └──────────────────────────────────┬───────────────────────────────────────────────┘
                                    ▼ events stream
 ┌──────────────────── WARM PATH (per window close, seconds) ───────────────────────┐
 │ Window Aggregator → Contextual Baseline + Attribution → Isolation Forest + SHAP  │
 │ → ETA live update → Event Correlator → Incident Store                            │
 └──────────────────────────────────┬───────────────────────────────────────────────┘
                                    ▼ incidents stream
 ┌──────────────────── COLD PATH (async, seconds–minutes) ──────────────────────────┐
 │ Gemini Incident Explainer (tool calling) · Lesson Generator · Shift Handover     │
 │ · Effectiveness Tracker · (future) model retraining                              │
 └──────────────────────────────────────────────────────────────────────────────────┘
                                    ▼
          PostgreSQL (+Timescale-ready)      Redis pub/sub → WebSocket gateway → React
```

**Why three paths:** safety latency must never depend on model inference or an external API. Each path fails independently: Gemini down ⇒ cold path degrades to templates; ML worker down ⇒ HUD still safe.

### 4.2 Edge/cloud split (architecture pitch; MVP runs all in one worker)

- `copilot_core/` is a **pure, IO-free, deterministic** Python package (state classifier, safety envelope, arbitrator, risk). Same code can run on an in-cab gateway with store-and-forward when connectivity drops — the realistic deployment for jobsites.
- Cloud runs warm/cold paths, fleet views, training.

### 4.3 Modular monolith, stream-partitioned

One codebase, multiple entrypoints: `api`, `worker-hot`, `worker-warm`, `worker-cold`, `simulator`. Modules talk via typed event contracts on Redis Streams, so any module can later be extracted to a service or moved to Kafka/Redpanda without changing contracts.

---

## 5. Data model & event contracts

### 5.1 Three data grains

**TelemetryFrame (1 Hz)**
```json
{"seq": 18231, "ts": "2026-09-23T10:15:00Z", "machine_id": "EXC001",
 "operator_id": "OP1001", "task_id": "TSK-882",
 "engine_rpm": 1950, "engine_temp_c": 92.0, "hydraulic_pressure_bar": 280.0,
 "fuel_rate_lph": 14.2, "speed_kmh": 3.5, "seatbelt": "UNFASTENED",
 "proximity": {"zone": "RED", "distance_m": 4.2, "source": "camera_rear"},
 "cycle_completed": false, "truck_present": false, "hauler_queue_len": 0,
 "gps": [37.7749, -122.4194], "sig": "hmac-sha256…"}
```

**ContextFrame (on change / 60 s)**
```json
{"ts": "…", "site_id": "SITE-A", "weather": "RAIN", "rainfall_mm_h": 12.4,
 "visibility_m": 180, "ambient_temp_c": 28.5, "wind_kmh": 22,
 "ground": "MUDDY", "daylight": true}
```

**WindowAggregate (computed, 5 min real / 30 s demo-compressed)**
`fuel_per_cycle, rpm_mean, rpm_std, hyd_p95, idle_ratio, cycle_time_mean, cycle_time_cv, temp_slope, alerts_count, ack_latency_mean`

### 5.2 Event contract (all engines emit this)
```json
{"event_id": "uuid", "type": "PROXIMITY_BREACH", "severity": "CRITICAL",
 "machine_id": "EXC001", "operator_id": "OP1001", "site_id": "SITE-A",
 "ts": "…", "source_engine": "safety_envelope@1.2",
 "evidence": {"distance_m": 4.2, "zone": "RED", "effective_red_radius_m": 5.2,
              "condition_multiplier": 1.3}}
```
Event types: `SEATBELT_VIOLATION, PROXIMITY_BREACH, PROXIMITY_PERSISTENT, OVERSPEED_CONDITION, HEALTH_THRESHOLD, IDLE_DEVIATION, OPERATIONAL_ANOMALY, ETA_SLIP, RISK_LEVEL_CHANGE, FATIGUE_INDICATOR, CHECKLIST_FAIL`.

### 5.3 Relational schema (PostgreSQL)
```
organizations ─< regions ─< sites ─< machines
users(id, role, site_ids[], …)           shifts(operator, machine, start, end)
tasks(id, site, machine, type, target_cycles, planned_start, status, planner_estimate_min)
telemetry(ts, machine_id, …)             -- hypertable when Timescale enabled
context_frames(ts, site_id, …)
events(id, type, severity, machine, operator, ts, evidence jsonb, engine_version)
incidents(id, severity, status, opened_at, closed_at, explanation jsonb)
incident_events(incident_id, event_id)
alerts(id, key, first_ts, last_ts, count, acked_by, acked_at)
knowledge_chunks(id, doc, heading, text)
lessons(id, incident_id, operator_id, content jsonb, delivered_at, completed_at, score)
lesson_outcomes(lesson_id, metric, before, after)
training_modules, instructor_slots, bookings, checklists, checklist_results
audit_log(id, ts, actor, action, target, payload jsonb, prev_hash, hash)   -- append-only
```

---

## 6. Engines — why and how

### 6.1 Machine State Classifier (hot)
**Why:** everything attention-related keys off *what the machine is doing*.
**How:** deterministic rules with dwell and **asymmetric hysteresis** (enter safe modes fast, leave them slowly).

| State | Condition |
|---|---|
| OFF | rpm = 0 |
| IDLE | rpm < 1000 ∧ hyd < 50 bar ∧ speed < 0.5 km/h, held ≥ 5 s |
| TRAVEL | speed ≥ 0.5 km/h |
| WORKING | hyd ≥ 120 bar or cycle activity in last 10 s |

UI mode: **HUD** when TRAVEL/WORKING (switch immediately); **Idle Hub** after IDLE held ≥ 30 s (configurable). Any motion → HUD in < 1 frame. This prevents UI flicker and guarantees the expanded screen never appears while the machine moves.

### 6.2 Condition-Adaptive Safety Envelope (hot)
**Why:** brief says working conditions must be considered; fixed thresholds are wrong in rain, mud, darkness.
**How:** base parameters × condition multipliers (illustrative, admin-configurable, every change audited).

| Parameter | Base | Rain | Low visibility / night | Muddy ground | Heat index > 35 °C | Wind > 40 km/h |
|---|---|---|---|---|---|---|
| Red radius | 4 m | ×1.3 | ×1.5 | ×1.2 | – | – |
| Orange radius | 7 m | ×1.3 | ×1.5 | ×1.2 | – | – |
| Travel speed cap | 6 km/h | 4 | 3 | 3 | – | – |
| Break prompt | – | – | – | – | every 45 min in idle windows | – |
| Lift/boom warning | – | – | – | – | – | caution on high-boom ops |

Multipliers combine as max-of (not product) to avoid absurd radii. The HUD shows the **live envelope** (e.g., "Rain: zones +30 %, speed cap 4 km/h").

Seatbelt rule: `UNFASTENED ∧ (TRAVEL ∨ WORKING)` → CRITICAL; `UNFASTENED ∧ IDLE ∧ engine on > 10 s` → CAUTION (ingress/egress grace).

Standards to cite in the pitch: ISO 16001 (object detection systems for earth-moving machinery), ISO 21815 (collision warning and avoidance), ISA-18.2 / EEMUA 191 (alarm management). Verify exact titles before putting them on slides.

### 6.3 Alert Arbitrator (hot) — fail-safe by design
**Why:** repeated identical chimes cause habituation; ISA-18.2 treats more than about 10 alarms per 10 min per operator as a flood.
**Rules (non-negotiable):**
1. The **first occurrence** of any alert key is always emitted.
2. **CRITICAL is never hidden.** Its visual stays latched while the condition holds; only the *audio* repeat is rate-limited (≥ 5 s).
3. Repeats of key `(machine, type, zone)` within W = 30 s are aggregated; count ≥ 3 or duration ≥ 15 s → a consolidated **PERSISTENT** alert at severity +1 with an action ("Reduce swing speed").
4. Acknowledged → audio muted for `ack_ttl` unless severity rises.
5. Flood guard: > 10 alerts / 10 min → HUD shows the top-1 alert plus a counter; the full list goes to Idle Hub and supervisor.

`priority = w_sev × (1 + ln(1+count)) × e^(−age/τ) × (unacked ? 1.5 : 1)`

Log **suppressed audio counts** so the supervisor can see what was consolidated (transparency).

### 6.4 Risk Trajectory Engine (hot)
**Why:** risk is momentum, not a snapshot; it must visibly recover when behaviour improves.
```
R_t = clamp(R_{t−Δ} · e^{−λΔt} + Σ_e w_e · c_cond , 0, 100)
λ from half-life: 5 min real (60 s demo)
w: SEATBELT 25, PROX_RED 30, PROX_ORANGE 10, OVERSPEED 15, ANOMALY 10, IDLE_DEV 5
c_cond = max condition multiplier (1.0–1.5)
Levels with hysteresis: ELEVATED enter 30 / exit 20 · HIGH enter 60 / exit 45
```
All engines use **event time from the payload**, never wall clock, so replays and tests are deterministic.

### 6.5 Machine Health Rules (hot)
Deterministic thresholds plus slope: `engine_temp > 105 °C`, `hyd > 320 bar`, `temp_slope > X °C/min`. Emits `HEALTH_THRESHOLD`, marks idle as MACHINE-attributed, raises a maintenance flag. No ML needed here, and saying so is itself good judgement.

### 6.6 Contextual Baseline & Downtime Attribution (warm)
**Why:** "excessive idling" is meaningless without context; blaming operators for truck queues destroys trust.
**Attribution per idle segment (first match wins):**
1. PLANNED — scheduled break
2. MACHINE — active health fault
3. WEATHER — weather stop rule active
4. SITE — `truck_present = false ∨ hauler_queue_len = 0` (nothing to load)
5. OPERATOR — none of the above

**Operator deviation:** `expected_idle_ratio(c)` for context `c = (task_type, weather, skill)` is an EWMA with **hierarchical backoff** when n < 20: operator×context → skill cohort×context → task_type → global (cold-start solved). The flag fires on `actual/expected ≥ 2.0 ∧ robust_z = (x − median)/(1.4826·MAD) > 2.5`, persisting ≥ 2 windows.

### 6.7 Multivariate Anomaly Detection (warm)
**Why:** catches combinations that no single rule captures (low cycles + normal RPM + high fuel).
**How:** `IsolationForest(n_estimators=200, contamination≈0.03)` on WindowAggregate features.
- Score calibration: `s = −score_samples(x)`, mapped to 0–1 by the training-set percentile; threshold at P97.
- Drivers: `shap.TreeExplainer(iforest)` → top-3 features ("fuel_per_cycle +2.8σ, cycle_time_cv +2.1σ").
- Framing: "this operational pattern is unusual," never "operator is bad."
- Validation: synthetic data has ~3 % injected labelled anomalies → report precision/recall on a held-out set.

### 6.8 Task ETA Engine (warm)
**Why:** the brief says "based on past data and environmental conditions"; judges will compare against the planner estimate they gave us.
**How:**
- Features: `task_type, weather, rainfall, ground, visibility, skill, machine_age, target_cycles/volume, hauler_count, ambient_temp`.
- Models: XGBoost P50 (`reg:absoluteerror`) for the point estimate and SHAP; XGBoost multi-quantile (`reg:quantileerror`, α = [0.1, 0.9]) for the band.
- **Factorized explanation:** SHAP on P50 with a **task-type-specific background set**, so the reference is the typical duration for this task type, not the fleet mean. Contributions are grouped into Weather / Ground / Operator / Machine / Site queue and shown in minutes. They sum exactly to the prediction, so the numbers always add up.
- **Live update:** `remaining = w·model_remaining + (1−w)·remaining_cycles/observed_cycle_rate`, with `w = 1 − progress`. An ETA slip over 10 % emits `ETA_SLIP`.
- Metrics to show: MAE vs the provided `Estimated Time` baseline, and P10–P90 coverage close to 80 %.

### 6.9 Event Correlator & Incidents (warm)
**Why:** supervisors need one story, not 40 alerts.
**How:** events on the same machine within a sliding 5 min window, with at least one ≥ WARNING, open or extend an incident. Incident severity is the max, escalated if the risk level is HIGH. It is stored with an ordered **timeline** ("What changed?"). The incident packet is the *only* thing the LLM sees.

### 6.10 Generative layer — Gemini (cold)
**Principle:** Gemini **explains, recommends, teaches**. It never detects, scores, or gates safety.

- **Tools (read-only, executed server-side with the caller's RBAC scope):** `get_incident(id)`, `get_machine_status(machine_id)`, `get_eta(task_id)`, `get_safety_events(machine_id, window)`, `get_operator_context(operator_id)`, `search_knowledge(query)`.
- **Output schema (enforced JSON):**
  ```json
  {"summary": "…", "probable_causes": [{"cause": "…", "evidence_refs": ["event_id"]}],
   "recommended_actions": ["…"], "training_refs": [{"chunk_id": "…"}],
   "confidence": "LOW|MEDIUM|HIGH"}
  ```
- **Grounding validator:** every `evidence_ref` and `chunk_id` must exist. On failure, retry once, then fall back to a deterministic template.
- **Hygiene:** 8 s timeout; cache responses by incident hash; operator free-text is fenced as data (prompt-injection defense); the model has no write tools.
- **Uses:** incident explanation (supervisor), micro-lesson + scenario quiz (operator), shift-handover brief, conversational "why?" on any card (P1).

### 6.11 Knowledge retrieval (cold)
About 10 self-written markdown docs (safety/seatbelt, proximity, adverse weather, idle management, fuel efficiency, pre-start inspection, heat stress, excavation basics), paraphrased from general best practice rather than copied from Cat manuals. They are chunked by heading and searched with BM25 (`rank_bm25`), filtered by incident-type tags. Every lesson cites its chunk IDs in the UI. Bolt-on later: pgvector hybrid search. Keyword retrieval is honest RAG at this scale.

### 6.12 Closed-loop training (cold)
Incident → retrieve chunks → Gemini generates a 30 s tip plus a 3-question **incident-replay scenario** ("Rear camera showed a person at 4 m in rain — what's your first action?"). This is the brief's "simulation module." Delivery waits for the next Idle Hub window. The **Effectiveness Tracker** compares the target metric (seatbelt compliance, zone breaches per hour, idle deviation) for N windows before and after → "Zone B breaches −62 % after lesson." Also included: video library (links) and instructor booking (slots → request → supervisor approve).

### 6.13 Daily bookends
- **Pre-start checklist** (P0): 6–8 walkaround items; completing it unlocks "Start shift"; a failed item emits `CHECKLIST_FAIL` → maintenance flag.
- **Fatigue indicator** (P1): continuous operation > 2 h without a break, or rising alert-acknowledgement latency → break suggestion in the idle window (a suggestion only, never enforcement).
- **Shift handover** (P1): Gemini summarizes the shift's incidents, machine notes and unfinished tasks for the next operator.

---

## 7. UI architecture

| View | Role | Content |
|---|---|---|
| Pre-start | Operator | Checklist, today's tasks, conditions and envelope |
| **Active HUD** | Operator | Status bars (safety/risk/health), cycle counter, ETA P50, envelope badge, arbitrated alerts. Nothing else. |
| **Idle Hub** | Operator | Task list, ETA factor breakdown, idle attribution, pending micro-lesson, training hub, "why?" chat |
| Site view | Supervisor | Machine cards by risk, open incidents, incident timeline + Gemini explanation, attribution analytics, assign training, approve bookings |
| Fleet | Fleet manager | Cross-site KPIs (utilization, idle split, incidents/100 h). One page. |
| Admin | Admin | **Demo director** (scenario injector), thresholds config, users, audit chain verify |

Design rules: tablet landscape 1280×800; touch targets of 56 px or more (gloves); high-contrast dark default with daylight toggle; colour never the sole signal (icon + text); no more than 3 elements in the HUD alert area.

Realtime: `/ws/machine/{id}` and `/ws/site/{id}`. The API fans out through Redis pub/sub, so multiple API replicas work unchanged.

---

## 8. Security & trust

| Control | MVP | Future |
|---|---|---|
| AuthN | JWT HS256, argon2 hashes | OIDC (Entra/Keycloak), RS256, short-lived + refresh |
| AuthZ | RBAC + site-scoped ABAC via JWT claims `{role, site_ids, machine_ids}` | Postgres Row-Level Security |
| Telemetry integrity | HMAC-SHA256 per machine key, monotonic `seq`, ±30 s timestamp window (replay defense) | mTLS device certs, key rotation |
| Audit | Hash-chained append-only log `hash = SHA256(prev_hash ‖ canonical_json(entry))`; `/audit/verify`; DB role without UPDATE/DELETE | WORM storage / external anchoring |
| LLM | Read-only scoped tools, schema-validated output, grounding check, input fencing | Red-team eval set |
| Privacy | Operator sees own data first; supervisor sees attribution, not raw surveillance feeds | Data retention policies, pseudonymized analytics |

Audited actions: login, incident acknowledgement and close, threshold change, training assignment, booking approval, role change, and demo injections (flagged).

---

## 9. Scalability & robustness

- **Ordering:** consumer groups do not preserve per-key order across consumers, so shard `telemetry:{hash(machine_id)%N}` with one consumer per shard. Each machine is processed in order and the system scales horizontally by adding shards.
- **Stateless workers:** per-machine state (classifier dwell, arbitrator counters, risk R_t, EWMA) lives in Redis hashes, so a crashed worker restarts without state loss. Pending messages are reclaimed with `XAUTOCLAIM`.
- **Idempotency:** dedupe on `(machine_id, seq)`; `XACK` only after persist.
- **Back-of-envelope:** 500 machines × 1 Hz = 500 msg/s. A single Redis node handles far more. Postgres inserts are batched (`COPY` / multi-row every 1 s). Timescale compression is the bolt-on for months of telemetry.
- **Degradation:** Gemini down → templates; ML down → HUD still safe; WebSocket drop → client auto-reconnect plus last-state snapshot endpoint.
- **Replay:** stored telemetry can be re-run through the pipeline. This drives the golden tests and the "What changed?" timeline.
- **Observability:** structured JSON logs (structlog), `/health` and `/ready`, stream-lag metric. Bolt-ons: Prometheus/Grafana, OpenTelemetry.

---

## 10. Tech stack (MVP) and bolt-on path

| Layer | MVP choice | Why | Bolt-on later |
|---|---|---|---|
| Frontend | React 18 + Vite + TypeScript, Tailwind, shadcn/ui, Recharts, Zustand, TanStack Query, react-router, lucide-react | Fast, typed, component kit for speed | PWA offline cache, Capacitor tablet app |
| API | Python 3.11+, FastAPI, Pydantic v2, Uvicorn | Async, typed contracts shared with ML code | Gunicorn workers, API gateway |
| DB access | SQLAlchemy 2.0 async + asyncpg, Alembic | Migrations from hour 1 | Read replicas |
| DB | PostgreSQL 16 (`timescale/timescaledb` image, hypertable optional) | One store for relational + time series | Timescale compression/continuous aggregates, pgvector |
| Stream/cache | Redis 7 Streams + pub/sub + hashes (`redis-py` asyncio) | Buffering, consumer groups, fan-out, state | Kafka/Redpanda (same contracts) |
| ML | pandas, numpy, scikit-learn, XGBoost ≥ 2.0, SHAP, joblib | Quantile objective, native SHAP support | MLflow registry, scheduled retrain |
| LLM | Gemini (current Flash-tier model, e.g. `gemini-2.5-flash`; confirm the latest at the event) via `google-genai` SDK, function calling + JSON schema | Tool calling, low latency, cheap | Model routing, eval harness |
| Retrieval | `rank_bm25` over markdown chunks | Zero infra, explainable | pgvector hybrid |
| Auth | PyJWT, pwdlib[argon2] | Minimal, correct | OIDC |
| Infra | Docker Compose (postgres, redis, api, worker-hot, worker-warm, worker-cold, simulator, web) | One command up | Kubernetes + autoscaling on stream lag |
| Quality | pytest (+ golden scenario replays), ruff | Safety logic must be tested | CI, load tests (Locust) |
| Logs | structlog | JSON logs from day 1 | OTel, Grafana |

---

## 11. Deliberately not building

Kafka, Kubernetes, microservices, LangChain/agents, fine-tuning, computer vision (proximity is assumed from Cat Detect-like sensors, which the brief explicitly allows: "available or assumed data"), blockchain, a vector DB. Each exclusion is a defended tradeoff, not an omission.

---

## 12. Numbers to show judges

- ETA: MAE vs planner estimate baseline (target ≥ 30 % reduction on synthetic hold-out), P10–P90 coverage ≈ 80 %
- Anomaly: precision/recall on injected anomalies
- Arbitration: raw alerts vs delivered interruptions in the demo scenario (e.g., 23 → 5, with zero critical suppressed)
- Hot-path latency p95 (ingest → HUD)
- Training loop: before/after metric delta
- Audit chain: verified ✓ / tamper-detected demo (edit one row → verification fails)

State clearly that all numbers come from synthetic data, and explain how the generator encodes physical correlations.

---

## 13. Risks & mitigations

| Risk | Mitigation |
|---|---|
| "You suppress safety alarms?" | Fail-safe rules §6.3; show the suppressed-audio log |
| "Synthetic data proves nothing" | Correlations are documented; the pipeline is data-agnostic; the ETA baseline to beat is the brief's own estimate column |
| LLM hallucination | Structured packet only, grounding validator, citations, template fallback |
| Surveillance concerns | Attribution-first, operator-first visibility, coaching framing |
| Demo failure | Seeded scenarios, demo director, cached Gemini responses, backup video |
| Frontend bottleneck | shadcn kit; supervisor/fleet views kept minimal; mock WS feed from hour 2 |
