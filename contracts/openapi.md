# API Endpoints

## Ingestion (Hot Path)
- `POST /ingest/telemetry` - Ingest 1 Hz telemetry frame (requires HMAC signature)
- `POST /ingest/context` - Ingest environmental context frame (no HMAC yet — trusted internal source)

## Auth
- `POST /auth/login` - Login with `{username, password}` → JWT access token
- `GET /auth/me` - Current user profile + `site_ids`
- `POST /auth/ws-ticket` - Short-lived (60s) ticket for the WebSocket gateway

## Tasks
- `GET /tasks/me/tasks` - Get tasks for the current operator
- `GET /tasks/sites/{site_id}/tasks` - Get all tasks for a site (SUPERVISOR/ADMIN)
- `GET /tasks/{task_id}/eta` - Latest `EtaEstimate` for a task (LIVE if present, else BASELINE, else `{status: "UNAVAILABLE"}`). Operator must own the task; supervisor/admin need site access. *(Batch 3F)*

## WebSocket
- `GET /ws/stream/{machine_id}?ticket=` - Real-time `UiPush` stream for one machine (site-scoped)

## Incidents *(Batch 3E/3F)*
- `GET /incidents?site_id=&machine_id=&status=&since=&limit=&offset=` → `{items: [IncidentSummary], total}`.
  RBAC: OPERATOR sees only their own incidents (`operator_id == self`); SUPERVISOR sees only sites they have access to (a `site_id` filter outside their scope → 403); ADMIN sees all.
- `GET /incidents/{id}` → incident summary + `explanation` (null until Batch 3H's cold worker runs). 404 if missing, 403 if out of the caller's scope.
- `GET /incidents/{id}/timeline` → ordered `[TimelineEntry]` (by `first_ts, id`), each with its `representative_event` (id, type, severity, ts, evidence).
- `POST /incidents/{id}/ack` (SUPERVISOR, ADMIN; site-scoped) - OPEN → ACKNOWLEDGED. 409 if not currently OPEN. Audited (`INCIDENT_ACK`).
- `POST /incidents/{id}/close` (SUPERVISOR, ADMIN; site-scoped) - body `{"note": "..."}` (1-500 chars). OPEN|ACKNOWLEDGED → CLOSED. 409 otherwise. Audited (`INCIDENT_CLOSE`, payload carries `note_sha256` not the note text).

## Machines *(Batch 3F)*
- `GET /machines/{machine_id}/snapshot` - Current state for WS reconnect: `{machine_id, site_id, snapshot_ts, hot, envelope, context, recent_events, latest_window, eta, open_incidents}`. Every field comes from Redis/DB directly; missing data is an explicit `null`/`UNAVAILABLE`, never synthesized. `hot.stale` is `true` when the last hot-worker update is >10s old.
- `GET /machines/{machine_id}/windows?limit=` - Recent `window_aggregates` rows + `totals_s` (idle seconds per cause, summed over the returned windows).

## Knowledge *(Batch 3G)*
- `GET /knowledge/chunks/{chunk_id}` - one knowledge-base chunk (`{chunk_id, doc_id, title, heading, text}`), for rendering citation chips against `evidence_refs`/`knowledge_refs`/`training_refs` in an incident explanation.

## Admin / System
- `GET /health` - Liveness check
- `GET /ready` - Readiness check (Redis reachability)
- `GET /audit/verify` - Verify audit log chain integrity (ADMIN)
- `GET /admin/workers` - `worker:status:*` hashes (warm/hot/correlator/cold) + consumer-group lag from `XINFO GROUPS` (ADMIN). *(Batch 3F)*

## Not yet implemented
- Training/lessons endpoints (Stage 4, out of scope for Stage 3)
- `/ws/site/{id}` (site-wide multiplex) — the frontend currently opens one `/ws/stream/{machine_id}` per machine card instead

## UiPush payload types (`contracts/events.py::UiPushType`)
`state_change | alert | alert_clear | risk | envelope | eta | idle_attribution | anomaly | lesson_ready | incident`

- `envelope` *(Batch 3.0)*: `{red_radius_m, orange_radius_m, speed_cap_kmh, condition_multiplier, active_conditions, notes}` — pushed only when `active_conditions` changes.
- `eta` *(Batch 3D)*: full `EtaEstimate` payload.
- `idle_attribution` *(Batch 3D)*: full `IdleAttribution` payload.
- `anomaly` *(Batch 3D)*: full `AnomalyResult` payload.
- `incident` *(Batch 3E/3F)*: `{action: "opened"|"extended"|"escalated"|"acknowledged"|"closed", incident: IncidentSummary}`. `extended` pushes are throttled to one per incident per `correlator_extend_push_throttle_s` (DB is always updated regardless — only the UI push is throttled).
