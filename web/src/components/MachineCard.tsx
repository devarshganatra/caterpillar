import { Link } from 'react-router-dom';
import { SignalHigh, SignalZero, ShieldAlert, AlertOctagon } from 'lucide-react';
import { cn } from '../lib/utils';
import { useMachineStream } from '../store/useMachineStream';
import { AnomalyBadge } from './intel/AnomalyBadge';
import { EtaChip } from './intel/EtaChip';

export function MachineCard({ machineId, initialTask }: { machineId: string, initialTask: any }) {
  const { status, currentState, riskLevel, alerts, eta, anomaly, openIncidents } = useMachineStream(machineId);

  const riskColor =
    riskLevel === 'CRITICAL' || riskLevel === 'HIGH' ? 'border-status-critical bg-status-critical/10 text-status-critical' :
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
        <div className="flex flex-col items-end gap-1.5">
          <div className="text-sm font-bold">{riskLevel}</div>
          {isLive ? <SignalHigh className="w-4 h-4 opacity-70" /> : <SignalZero className="w-4 h-4 opacity-70 animate-pulse" />}
        </div>
      </div>

      <div className="flex items-center justify-between mb-3">
        <EtaChip eta={eta} compact />
        <AnomalyBadge anomaly={anomaly} />
      </div>

      <div className="space-y-2 mt-2 min-h-[44px]">
        {alerts.length > 0 ? (
          alerts.map((a) => (
            <div key={a.event_id} className="text-xs flex items-center gap-2">
              <ShieldAlert className="w-3 h-3 shrink-0" />
              <span className="truncate">{a.type}</span>
            </div>
          ))
        ) : (
          <div className="text-xs opacity-50 italic">No active alerts</div>
        )}
      </div>

      <div className="mt-4 pt-4 border-t border-border/20 flex items-center justify-between text-xs opacity-80">
        <span>
          Task: {initialTask.id} &middot; Operator: {initialTask.operator_id.split('-')[0]}
        </span>
        {openIncidents.length > 0 && (
          <Link
            to={`/supervisor/incidents/${openIncidents[0].id}`}
            className="flex items-center gap-1 font-semibold text-status-critical hover:underline shrink-0"
          >
            <AlertOctagon className="w-3.5 h-3.5" />
            {openIncidents.length} open
          </Link>
        )}
      </div>
    </div>
  );
}
