# API Endpoints

## Ingestion (Hot Path)
- `POST /ingest/telemetry` - Ingest 1 Hz telemetry frame (requires HMAC signature)
- `POST /ingest/context` - Ingest environmental context frame (requires HMAC signature)

## Auth
- `POST /auth/login` - Login to receive JWT

## Tasks
- `GET /me/tasks` - Get tasks for the current operator (today)
- `GET /sites/{site_id}/tasks` - Get all tasks for a site (requires supervisor role)

## Core & Streaming (Internal/WS)
- `/ws/machine/{id}` - Subscribe to real-time machine telemetry and alerts
- `/ws/site/{id}` - Subscribe to site-wide machine updates

## Incidents (Warm/Cold Path)
- `GET /incidents` - List incidents (filterable by site, machine, status)
- `GET /incidents/{id}` - Get incident details including Gemini explanation and timeline
- `POST /incidents/{id}/ack` - Acknowledge incident
- `POST /incidents/{id}/close` - Close incident

## Training
- `GET /training/lessons/pending` - Get pending lessons for operator
- `POST /training/lessons/{id}/complete` - Mark lesson complete with score

## Admin / System
- `GET /health` - System health check
- `GET /ready` - System readiness check
- `GET /audit/verify` - Verify audit log chain integrity
