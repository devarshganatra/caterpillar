import { useEffect, useState, useRef } from 'react';
import { apiFetch } from '../lib/api';
import { SignalHigh, SignalZero, ShieldAlert } from 'lucide-react';
import { cn } from '../lib/utils';
import type { UiPush } from '../store/MachineContext';

export function MachineCard({ machineId, initialTask }: { machineId: string, initialTask: any }) {
  const [status, setStatus] = useState<'CONNECTING' | 'CONNECTED' | 'DISCONNECTED' | 'STALE'>('DISCONNECTED');
  const [currentState, setCurrentState] = useState('UNKNOWN');
  const [riskLevel, setRiskLevel] = useState('NORMAL');
  const [alerts, setAlerts] = useState<any[]>([]);

  const wsRef = useRef<WebSocket | null>(null);
  const lastEventTsRef = useRef<number>(0);
  const staleIntervalRef = useRef<number | null>(null);

  useEffect(() => {
    staleIntervalRef.current = window.setInterval(() => {
      if (wsRef.current?.readyState === WebSocket.OPEN && lastEventTsRef.current > 0) {
        if (Date.now() - lastEventTsRef.current > 10000) {
          setStatus(prev => prev === 'CONNECTED' ? 'STALE' : prev);
        }
      }
    }, 1000);
    return () => clearInterval(staleIntervalRef.current!);
  }, []);

  useEffect(() => {
    let isMounted = true;
    let ws: WebSocket | null = null;
    let reconnectTimeout: number;

    async function connect() {
      if (!isMounted) return;
      try {
        setStatus('CONNECTING');
        const res = await apiFetch("/auth/ws-ticket", { method: "POST" });
        if (!res.ok) throw new Error("Ticket failed");
        const { ticket } = await res.json();
        
        ws = new WebSocket(`ws://localhost:8000/ws/stream/${machineId}?ticket=${ticket}`);
        wsRef.current = ws;

        ws.onopen = () => {
          if (!isMounted) return;
          setStatus('CONNECTED');
          lastEventTsRef.current = Date.now();
        };

        ws.onmessage = (event) => {
          if (!isMounted) return;
          lastEventTsRef.current = Date.now();
          setStatus('CONNECTED');
          
          try {
            const data: UiPush = JSON.parse(event.data);
            if (data.type === 'state_change') setCurrentState(data.payload.state);
            else if (data.type === 'risk') setRiskLevel(data.payload.level);
            else if (data.type === 'alert') {
              setAlerts(prev => {
                const existing = prev.find(a => a.id === data.payload.id);
                if (existing) return prev.map(a => a.id === data.payload.id ? data.payload : a);
                return [...prev, data.payload].slice(-3);
              });
            } else if (data.type === 'alert_clear') {
              setAlerts(prev => prev.filter(a => a.id !== data.payload.id));
            }
          } catch (e) {}
        };

        ws.onclose = () => {
          if (!isMounted) return;
          setStatus('DISCONNECTED');
          reconnectTimeout = window.setTimeout(connect, 2000);
        };
      } catch (err) {
        if (isMounted) {
          setStatus('DISCONNECTED');
          reconnectTimeout = window.setTimeout(connect, 2000);
        }
      }
    }

    connect();

    return () => {
      isMounted = false;
      clearTimeout(reconnectTimeout);
      if (ws) {
        ws.onclose = null;
        ws.close();
      }
    };
  }, [machineId]);

  const riskColor = 
    riskLevel === 'CRITICAL' || riskLevel === 'HIGH' ? 'border-destructive bg-destructive/10 text-destructive' :
    riskLevel === 'ELEVATED' ? 'border-status-warning bg-status-warning/10 text-status-warning' :
    'border-status-normal bg-status-normal/10 text-status-normal';

  const isLive = status === 'CONNECTED';

  return (
    <div className={cn("glass-card p-4 flex flex-col justify-between transition-colors", riskColor)}>
      <div className="flex justify-between items-start mb-4">
        <div>
          <h3 className="text-xl font-bold font-mono">{machineId}</h3>
          <p className="text-xs uppercase opacity-80 mt-1">{currentState}</p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <div className="text-sm font-bold">{riskLevel}</div>
          {isLive ? <SignalHigh className="w-4 h-4 opacity-70" /> : <SignalZero className="w-4 h-4 opacity-70 animate-pulse" />}
        </div>
      </div>

      <div className="space-y-2 mt-4 min-h-[60px]">
        {alerts.length > 0 ? (
          alerts.map((a, i) => (
            <div key={i} className="text-xs flex items-center gap-2">
              <ShieldAlert className="w-3 h-3" />
              <span className="truncate">{a.type}</span>
            </div>
          ))
        ) : (
          <div className="text-xs opacity-50 italic">No active alerts</div>
        )}
      </div>
      
      <div className="mt-4 pt-4 border-t border-border/20 text-xs opacity-60">
        Task: {initialTask.id} • Operator: {initialTask.operator_id.split('-')[0]}
      </div>
    </div>
  );
}
