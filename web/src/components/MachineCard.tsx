import { Link } from 'react-router-dom';
import { SignalHigh, SignalZero, ShieldAlert, AlertOctagon, Activity } from 'lucide-react';
import { cn } from '../lib/utils';
import { useMachineStream } from '../store/useMachineStream';
import { AnomalyBadge } from './intel/AnomalyBadge';
import { EtaChip } from './intel/EtaChip';

function stateBadgeClass(state: string) {
  const map: Record<string, string> = {
    WORKING: 'state-working', TRAVEL: 'state-travel',
    IDLE: 'state-idle', OFF: 'state-off',
  };
  return map[state] ?? 'state-off';
}

export function MachineCard({ machineId, initialTask }: { machineId: string; initialTask: any }) {
  const { status, currentState, riskLevel, riskScore, alerts, eta, anomaly, openIncidents } = useMachineStream(machineId);
  const isLive = status === 'CONNECTED';

  const riskBorder =
    riskLevel === 'HIGH'     ? 'border-status-critical/50 shadow-[0_0_0_1px_rgb(239_68_68/_0.2)]' :
    riskLevel === 'ELEVATED' ? 'border-status-warning/50' : 'border-border/60';

  return (
    <div className={cn('glass-card p-5 flex flex-col gap-4 transition-all duration-300', riskBorder)}>
      {/* Top row: machine ID + state + connection */}
      <div className="flex items-start justify-between">
        <div>
          <div className="font-black text-lg font-mono text-foreground tracking-tight">{machineId}</div>
          <div className="flex items-center gap-2 mt-1.5">
            <span className={cn('state-badge text-[10px]', stateBadgeClass(currentState))}>
              <span className="w-1 h-1 rounded-full bg-current" />
              {currentState}
            </span>
          </div>
        </div>
        <div className="flex flex-col items-end gap-1.5">
          <div className={cn(
            'risk-pill text-[10px]',
            riskLevel === 'HIGH'     ? 'risk-pill-high' :
            riskLevel === 'ELEVATED' ? 'risk-pill-elevated' : 'risk-pill-normal'
          )}>
            <span className={cn(
              'w-1 h-1 rounded-full',
              riskLevel === 'HIGH' ? 'bg-status-critical' :
              riskLevel === 'ELEVATED' ? 'bg-status-warning' : 'bg-status-normal'
            )} />
            {riskLevel}
            {riskScore != null && (
              <span className="intel-num opacity-70 ml-0.5">{riskScore.toFixed(2)}</span>
            )}
          </div>
          <div className={cn(
            'flex items-center gap-1 text-[10px] font-medium',
            isLive ? 'text-status-normal' : 'text-status-offline'
          )}>
            {isLive ? <SignalHigh className="w-3 h-3" /> : <SignalZero className="w-3 h-3 animate-pulse" />}
            {status}
          </div>
        </div>
      </div>

      {/* ETA + Anomaly row */}
      <div className="flex items-center justify-between gap-3">
        <EtaChip eta={eta} compact />
        <AnomalyBadge anomaly={anomaly} />
      </div>

      {/* Alerts */}
      <div className="min-h-[32px]">
        {alerts.length > 0 ? (
          <div className="space-y-1.5">
            {alerts.map((a) => (
              <div
                key={a.event_id}
                className={cn(
                  'text-[11px] flex items-center gap-2 px-2.5 py-1.5 rounded',
                  a.severity === 'CRITICAL' ? 'bg-status-critical/10 text-status-critical' :
                  a.severity === 'WARNING'  ? 'bg-status-warning/10  text-status-warning' :
                  'bg-secondary text-muted-foreground'
                )}
              >
                <ShieldAlert className="w-3 h-3 shrink-0" />
                <span className="truncate font-medium">{a.type.replace(/_/g, ' ')}</span>
              </div>
            ))}
          </div>
        ) : (
          <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground/50">
            <Activity className="w-3 h-3" />
            No active alerts
          </div>
        )}
      </div>

      {/* Footer: task/operator + incident link */}
      <div className="pt-3 border-t border-border/30 flex items-center justify-between text-[11px] text-muted-foreground">
        <div className="min-w-0 truncate">
          <span className="font-mono">{initialTask?.id ?? '—'}</span>
          {initialTask?.operator_id && (
            <span className="ml-2 opacity-60">· OP {initialTask.operator_id.slice(0, 8)}</span>
          )}
        </div>
        {openIncidents.length > 0 && (
          <Link
            to={`/supervisor/incidents/${openIncidents[0].id}`}
            className="shrink-0 flex items-center gap-1 text-status-critical font-semibold hover:underline ml-2"
          >
            <AlertOctagon className="w-3.5 h-3.5" />
            {openIncidents.length} open
          </Link>
        )}
      </div>
    </div>
  );
}
