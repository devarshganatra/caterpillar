import { createContext, useContext, useState, useEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import { apiFetch } from '../lib/api';

export type ConnectionStatus = "CONNECTING" | "CONNECTED" | "DISCONNECTED" | "STALE";

export interface UiPush {
  type: string;
  machine_id: string;
  ts: string;
  payload: any;
}

interface MachineContextType {
  machineId: string | null;
  setMachineId: (id: string | null) => void;
  status: ConnectionStatus;
  currentState: string;
  riskLevel: string;
  alerts: any[];
  envelope: any | null;
}

const MachineContext = createContext<MachineContextType | undefined>(undefined);

const STALE_TIMEOUT_MS = 10000; // 10 seconds without an event = stale

export function MachineProvider({ children }: { children: ReactNode }) {
  const [machineId, setMachineId] = useState<string | null>(null);
  const [status, setStatus] = useState<ConnectionStatus>("DISCONNECTED");
  
  // Raw State Data
  const [currentState, setCurrentState] = useState("OFF");
  const [riskLevel, setRiskLevel] = useState("NORMAL");
  const [alerts, setAlerts] = useState<any[]>([]);
  const [envelope, setEnvelope] = useState<any | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<number | null>(null);
  const backoffRef = useRef(1000); // Start at 1s
  const lastEventTsRef = useRef<number>(0);
  const staleIntervalRef = useRef<number | null>(null);

  useEffect(() => {
    // Stale timer
    staleIntervalRef.current = window.setInterval(() => {
      if (wsRef.current?.readyState === WebSocket.OPEN && lastEventTsRef.current > 0) {
        if (Date.now() - lastEventTsRef.current > STALE_TIMEOUT_MS) {
          setStatus(prev => prev === "CONNECTED" ? "STALE" : prev);
        }
      }
    }, 1000);

    return () => {
      if (staleIntervalRef.current) clearInterval(staleIntervalRef.current);
    };
  }, []);

  const connect = async (mId: string) => {
    try {
      setStatus("CONNECTING");
      
      // 1. Get Ticket
      const res = await apiFetch("/auth/ws-ticket", { method: "POST" });
      if (!res.ok) throw new Error("Failed to get WS ticket");
      const { ticket } = await res.json();

      // 2. Connect
      // We expect Vite proxy or direct backend URL. Hardcoding to localhost:8000 for demo
      const wsUrl = `ws://localhost:8000/ws/stream/${mId}?ticket=${ticket}`;
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        setStatus("CONNECTED");
        backoffRef.current = 1000; // reset backoff
        lastEventTsRef.current = Date.now();
      };

      ws.onmessage = (event) => {
        lastEventTsRef.current = Date.now();
        setStatus("CONNECTED"); // Clear stale if active
        
        try {
          const data: UiPush = JSON.parse(event.data);
          handleUiPush(data);
        } catch (e) {
          console.error("Failed to parse WS message", e);
        }
      };

      ws.onclose = () => {
        setStatus("DISCONNECTED");
        scheduleReconnect(mId);
      };

      ws.onerror = () => {
        ws.close();
      };

    } catch (err) {
      setStatus("DISCONNECTED");
      scheduleReconnect(mId);
    }
  };

  const scheduleReconnect = (mId: string) => {
    if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current);
    reconnectTimeoutRef.current = window.setTimeout(() => {
      backoffRef.current = Math.min(backoffRef.current * 1.5, 30000); // Max 30s
      connect(mId);
    }, backoffRef.current);
  };

  const disconnect = () => {
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
    if (wsRef.current) {
      wsRef.current.onclose = null; // prevent reconnect
      wsRef.current.close();
      wsRef.current = null;
    }
    setStatus("DISCONNECTED");
  };

  useEffect(() => {
    if (machineId) {
      connect(machineId);
    } else {
      disconnect();
    }
    return () => disconnect();
  }, [machineId]);

  const handleUiPush = (push: UiPush) => {
    // Only accept events for our active machine
    if (push.machine_id !== machineId) return;

    // Handle different push types based on existing backend contracts
    switch (push.type) {
      case 'state_change':
        // Ensure we don't jump backwards by relying on timestamps or sequence
        // For simplicity, we just set it here.
        setCurrentState(push.payload.state);
        break;
      case 'alert':
        setAlerts(prev => {
          const existing = prev.find(a => a.id === push.payload.id);
          if (existing) return prev.map(a => a.id === push.payload.id ? push.payload : a);
          return [...prev, push.payload].slice(-3); // Keep max 3 for UI
        });
        break;
      case 'alert_clear':
        setAlerts(prev => prev.filter(a => a.id !== push.payload.id));
        break;
      case 'risk':
        setRiskLevel(push.payload.level);
        break;
      case 'envelope':
        setEnvelope(push.payload);
        break;
      default:
        break;
    }
  };

  return (
    <MachineContext.Provider value={{
      machineId,
      setMachineId,
      status,
      currentState,
      riskLevel,
      alerts,
      envelope
    }}>
      {children}
    </MachineContext.Provider>
  );
}

export function useMachine() {
  const context = useContext(MachineContext);
  if (context === undefined) {
    throw new Error('useMachine must be used within a MachineProvider');
  }
  return context;
}
