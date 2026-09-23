import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch } from "../lib/api";
import type {
  EtaEstimate, IdleAttribution, AnomalyResult, IncidentSummary, EnvelopeState, MachineSnapshot,
} from "../lib/types";

// Stage 3 Batch 3I: the single source of truth for "what does the live
// state of one machine look like", shared by MachineContext (the
// operator's own machine) and MachineCard (a supervisor's fleet grid) so
// the two never drift into two different WS implementations again.
//
// Fixes carried over from the plan's G6/G7/G11:
//   G6 — reads ui_mode from the snapshot/state_change payload instead of
//        waiting for a `currentState === "IDLE_HUB"` that the backend
//        never actually sends (state and ui_mode are separate fields).
//   G7 — risk_level comes from state_change.payload.risk_level (and the
//        snapshot's hot.risk_level), not a `risk` push with `.level` that
//        the hot worker never emits.
//   G11 — alert de-dupe keys on `event_id` (what the backend sends), not
//        a nonexistent `.id`.

export type ConnectionStatus = "CONNECTING" | "CONNECTED" | "DISCONNECTED" | "STALE";

export interface UiPush {
  type: string;
  machine_id: string;
  ts: string;
  payload: any;
}

export interface AlertItem {
  event_id: string;
  type: string;
  severity: "INFO" | "WARNING" | "CRITICAL";
  evidence?: Record<string, unknown>;
  action_hint?: string;
  count?: number;
}

export type EtaOrUnavailable = EtaEstimate | { status: "UNAVAILABLE"; unavailable_reason?: string };

export interface MachineStreamState {
  status: ConnectionStatus;
  snapshotLoaded: boolean;
  currentState: string;
  uiMode: "HUD" | "IDLE_HUB";
  riskLevel: string;
  riskScore: number | null;
  alerts: AlertItem[];
  envelope: EnvelopeState | null;
  eta: EtaOrUnavailable | null;
  idleAttribution: IdleAttribution | null;
  anomaly: AnomalyResult | null;
  openIncidents: IncidentSummary[];
}

const WS_BASE = "ws://localhost:8000";
const STALE_TIMEOUT_MS = 10000;
const INITIAL_BACKOFF_MS = 1000;
const MAX_BACKOFF_MS = 30000;

const INITIAL_STATE: Omit<MachineStreamState, "status" | "snapshotLoaded"> = {
  currentState: "OFF",
  uiMode: "HUD",
  riskLevel: "NORMAL",
  riskScore: null,
  alerts: [],
  envelope: null,
  eta: null,
  idleAttribution: null,
  anomaly: null,
  openIncidents: [],
};

export function useMachineStream(machineId: string | null): MachineStreamState {
  const [status, setStatus] = useState<ConnectionStatus>("DISCONNECTED");
  const [snapshotLoaded, setSnapshotLoaded] = useState(false);
  const [state, setState] = useState(INITIAL_STATE);

  const wsRef = useRef<WebSocket | null>(null);
  const backoffRef = useRef(INITIAL_BACKOFF_MS);
  const lastEventAtRef = useRef(0);
  const snapshotTsRef = useRef<string>("");

  // Stale watchdog: if the socket is open but nothing has arrived recently,
  // surface it rather than silently showing frozen data as if it were live.
  useEffect(() => {
    const interval = window.setInterval(() => {
      if (wsRef.current?.readyState === WebSocket.OPEN && lastEventAtRef.current > 0) {
        if (Date.now() - lastEventAtRef.current > STALE_TIMEOUT_MS) {
          setStatus((prev) => (prev === "CONNECTED" ? "STALE" : prev));
        }
      }
    }, 1000);
    return () => window.clearInterval(interval);
  }, []);

  const applySnapshot = useCallback((snap: MachineSnapshot) => {
    snapshotTsRef.current = snap.snapshot_ts;
    setState({
      currentState: snap.hot?.state ?? "OFF",
      uiMode: (snap.hot?.ui_mode as "HUD" | "IDLE_HUB") ?? "HUD",
      riskLevel: snap.hot?.risk_level ?? "NORMAL",
      riskScore: snap.hot?.risk_score ?? null,
      alerts: snap.recent_events.map((e) => ({
        event_id: e.id, type: e.type, severity: e.severity as AlertItem["severity"], evidence: e.evidence,
      })),
      envelope: snap.envelope,
      eta: snap.eta,
      idleAttribution: snap.latest_window?.idle_attribution ?? null,
      anomaly: snap.latest_window?.anomaly ?? null,
      openIncidents: snap.open_incidents ?? [],
    });
    setSnapshotLoaded(true);
  }, []);

  const handlePush = useCallback((push: UiPush) => {
    if (push.machine_id !== machineId) return;
    // Reconnect race guard: a push that raced ahead of (or predates) the
    // snapshot fetch on this connection is redundant with what the
    // snapshot already reflects — ignore it rather than risk stepping
    // backwards.
    if (snapshotTsRef.current && push.ts < snapshotTsRef.current) return;

    setState((prev) => {
      switch (push.type) {
        case "state_change":
          return {
            ...prev,
            currentState: push.payload.state ?? prev.currentState,
            uiMode: (push.payload.ui_mode as "HUD" | "IDLE_HUB") ?? prev.uiMode,
            riskLevel: push.payload.risk_level ?? prev.riskLevel,
            riskScore: push.payload.risk_score !== undefined ? parseFloat(push.payload.risk_score) : prev.riskScore,
          };
        case "envelope":
          return { ...prev, envelope: push.payload };
        case "eta":
          return { ...prev, eta: push.payload };
        case "idle_attribution":
          return { ...prev, idleAttribution: push.payload };
        case "anomaly":
          return { ...prev, anomaly: push.payload };
        case "alert": {
          const key = push.payload.event_id;
          const existing = prev.alerts.find((a) => a.event_id === key);
          const alerts = existing
            ? prev.alerts.map((a) => (a.event_id === key ? { ...a, ...push.payload } : a))
            : [...prev.alerts, push.payload as AlertItem];
          return { ...prev, alerts: alerts.slice(-3) };
        }
        case "alert_clear":
          return { ...prev, alerts: prev.alerts.filter((a) => a.event_id !== push.payload.event_id) };
        case "incident": {
          const incident: IncidentSummary | undefined = push.payload.incident;
          if (!incident) return prev;
          if (incident.status === "CLOSED") {
            return { ...prev, openIncidents: prev.openIncidents.filter((i) => i.id !== incident.id) };
          }
          const exists = prev.openIncidents.some((i) => i.id === incident.id);
          const openIncidents = exists
            ? prev.openIncidents.map((i) => (i.id === incident.id ? incident : i))
            : [incident, ...prev.openIncidents];
          return { ...prev, openIncidents };
        }
        default:
          return prev;
      }
    });
  }, [machineId]);

  useEffect(() => {
    if (!machineId) {
      setStatus("DISCONNECTED");
      setSnapshotLoaded(false);
      setState(INITIAL_STATE);
      return;
    }

    let isMounted = true;
    let ws: WebSocket | null = null;
    let reconnectTimeout: number;
    setSnapshotLoaded(false);
    snapshotTsRef.current = "";

    async function connect() {
      if (!isMounted) return;
      try {
        setStatus("CONNECTING");
        const ticketRes = await apiFetch("/auth/ws-ticket", { method: "POST" });
        if (!ticketRes.ok) throw new Error("ws ticket request failed");
        const { ticket } = await ticketRes.json();

        ws = new WebSocket(`${WS_BASE}/ws/stream/${machineId}?ticket=${ticket}`);
        wsRef.current = ws;

        ws.onopen = async () => {
          if (!isMounted) return;
          backoffRef.current = INITIAL_BACKOFF_MS;
          lastEventAtRef.current = Date.now();
          try {
            const snapRes = await apiFetch(`/machines/${machineId}/snapshot`);
            if (snapRes.ok && isMounted) applySnapshot(await snapRes.json());
          } catch {
            // snapshot fetch failing is not fatal — the live stream will
            // still populate state as pushes arrive.
          }
          if (isMounted) setStatus("CONNECTED");
        };

        ws.onmessage = (event) => {
          if (!isMounted) return;
          lastEventAtRef.current = Date.now();
          setStatus((prev) => (prev === "CONNECTING" ? prev : "CONNECTED"));
          try {
            handlePush(JSON.parse(event.data));
          } catch {
            // malformed push: drop it, keep the connection alive.
          }
        };

        ws.onclose = () => {
          if (!isMounted) return;
          setStatus("DISCONNECTED");
          reconnectTimeout = window.setTimeout(() => {
            backoffRef.current = Math.min(backoffRef.current * 1.5, MAX_BACKOFF_MS);
            connect();
          }, backoffRef.current);
        };

        ws.onerror = () => ws?.close();
      } catch {
        if (isMounted) {
          setStatus("DISCONNECTED");
          reconnectTimeout = window.setTimeout(connect, backoffRef.current);
        }
      }
    }

    connect();

    return () => {
      isMounted = false;
      window.clearTimeout(reconnectTimeout);
      if (ws) {
        ws.onclose = null;
        ws.close();
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [machineId]);

  return { status, snapshotLoaded, ...state };
}
