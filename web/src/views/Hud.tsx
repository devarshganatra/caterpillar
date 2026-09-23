import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMachine } from '../store/MachineContext';
import { ShieldAlert, SignalHigh, SignalZero } from 'lucide-react';
import { cn } from '../lib/utils';
import { ThreeDVis } from '../components/ThreeDVis';
import { EtaChip } from '../components/intel/EtaChip';

export function Hud() {
  const { machineId, status, currentState, uiMode, riskLevel, alerts, envelope, eta } = useMachine();
  const navigate = useNavigate();

  useEffect(() => {
    if (!machineId) {
      navigate('/operator/prestart');
    }
  }, [machineId, navigate]);

  useEffect(() => {
    if (uiMode === 'IDLE_HUB') {
      navigate('/operator/idle');
    }
  }, [uiMode, navigate]);

  const isLive = status === 'CONNECTED';
  
  // Risk colors mapping
  const riskColor = 
    riskLevel === 'CRITICAL' || riskLevel === 'HIGH' ? 'text-destructive bg-destructive/10 border-destructive' :
    riskLevel === 'ELEVATED' ? 'text-status-warning bg-status-warning/10 border-status-warning' :
    'text-status-normal bg-status-normal/10 border-status-normal';

  return (
    <div className="min-h-screen relative overflow-hidden bg-zinc-950 p-6 flex flex-col justify-between">
      {/* Top Bar */}
      <div className="flex justify-between items-start z-10">
        
        {/* Left: Machine Info & Connection */}
        <div className="flex gap-4">
          <div className="glass-card px-4 py-2 flex items-center gap-2">
            <span className="font-mono text-xl font-bold">{machineId}</span>
            <span className="text-xs text-muted-foreground uppercase">{currentState}</span>
          </div>

          <div className={cn(
            "glass-card px-4 py-2 flex items-center gap-2 font-mono text-sm uppercase transition-colors",
            !isLive ? "border-destructive text-destructive" : "text-status-normal"
          )}>
            {!isLive ? <SignalZero className="w-4 h-4 animate-pulse" /> : <SignalHigh className="w-4 h-4" />}
            {status}
          </div>
        </div>

        {/* Right: Live ETA */}
        <EtaChip eta={eta} />
      </div>

      {/* Center 3D Vis Placeholder (Batch 5C.5) */}
      <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
        <ThreeDVis />
      </div>

      {/* Bottom Bar: Status & Alerts */}
      <div className="z-10 grid grid-cols-1 md:grid-cols-3 gap-6 items-end">
        
        {/* Left: Health & Envelope */}
        <div className="space-y-4">
          <div className="glass-card p-4 space-y-2 border-l-4 border-l-primary">
            <div className="text-xs text-muted-foreground uppercase font-bold tracking-wider">Active Envelope</div>
            {envelope ? (
              <div className="font-mono text-sm">
                <div>Cap: {envelope.speed_cap_kmh} km/h</div>
                {envelope.notes && <div className="text-xs text-muted-foreground">{envelope.notes}</div>}
              </div>
            ) : (
              <div className="text-sm text-muted-foreground italic">No restrictions</div>
            )}
          </div>
        </div>

        {/* Center: Alerts Zone */}
        <div className="space-y-3 flex flex-col justify-end min-h-[120px]">
          {alerts.map((alert, idx) => (
            <div
              key={alert.event_id || idx}
              className={cn(
                "glass-card p-3 flex items-center gap-3 animate-in slide-in-from-bottom-2",
                alert.severity === 'CRITICAL' ? 'border-destructive bg-destructive/10' :
                alert.severity === 'WARNING' ? 'border-status-warning bg-status-warning/10' : ''
              )}
            >
              <ShieldAlert className={alert.severity === 'CRITICAL' ? 'text-destructive' : 'text-status-warning'} />
              <div>
                <div className="font-bold uppercase text-sm">{alert.type}</div>
                <div className="text-xs opacity-80">{alert.evidence?.details || 'Deteriorating condition detected'}</div>
              </div>
            </div>
          ))}
        </div>

        {/* Right: Risk Bar */}
        <div className="flex justify-end">
          <div className={cn("glass-card p-4 border-r-4 min-w-[200px] text-right", riskColor)}>
            <div className="text-xs uppercase font-bold tracking-wider opacity-80">Overall Risk</div>
            <div className="text-3xl font-black mt-1 tracking-tighter">{riskLevel}</div>
          </div>
        </div>

      </div>

      {/* Disconnected Overlay */}
      {!isLive && (
        <div className="absolute inset-0 bg-background/80 backdrop-blur-sm z-50 flex flex-col items-center justify-center">
          <SignalZero className="w-16 h-16 text-destructive mb-4" />
          <h2 className="text-2xl font-bold">Live Data Unavailable</h2>
          <p className="text-muted-foreground mt-2">Attempting to reconnect...</p>
        </div>
      )}
    </div>
  );
}
