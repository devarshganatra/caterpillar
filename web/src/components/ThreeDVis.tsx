
import { useMachine } from '../store/MachineContext';

export function ThreeDVis() {
  const { currentState, riskLevel } = useMachine();
  
  // Simple CSS 3D representation of the machine state
  const isWorking = currentState === 'WORKING';
  const isMoving = currentState === 'TRAVEL';
  
  // A simple bobbing animation if active
  const animateClass = isWorking ? 'animate-bounce' : isMoving ? 'animate-pulse' : '';

  const colorClass = 
    riskLevel === 'CRITICAL' ? 'bg-destructive shadow-[0_0_30px_rgba(239,68,68,0.6)]' :
    riskLevel === 'HIGH' ? 'bg-destructive/80' :
    riskLevel === 'ELEVATED' ? 'bg-status-warning shadow-[0_0_30px_rgba(249,115,22,0.4)]' :
    'bg-primary shadow-[0_0_30px_rgba(250,204,21,0.2)]';

  return (
    <div className="relative w-64 h-64 flex flex-col items-center justify-center perspective-[1000px]">
      
      {/* 2.5D CSS representation of a machine */}
      <div className={`relative w-32 h-24 transform-style-3d transition-transform duration-1000 rotate-x-12 ${isMoving ? '-rotate-y-12' : 'rotate-y-12'}`}>
        
        {/* Cab */}
        <div className={`absolute top-0 left-8 w-16 h-12 rounded-t-lg border-2 border-border/50 ${colorClass} ${animateClass} transition-colors duration-500`} />
        
        {/* Body */}
        <div className={`absolute bottom-0 left-0 w-32 h-12 rounded-lg border-2 border-border/50 ${colorClass} ${animateClass} transition-colors duration-500`} />
        
        {/* Tracks/Wheels */}
        <div className="absolute -bottom-4 left-2 w-28 h-6 bg-zinc-900 rounded-full border border-zinc-700 overflow-hidden">
          {/* Animated track lines */}
          {(isWorking || isMoving) && (
            <div className="w-[200%] h-full bg-[repeating-linear-gradient(90deg,transparent,transparent_4px,rgba(255,255,255,0.1)_4px,rgba(255,255,255,0.1)_8px)] animate-[spin_2s_linear_infinite]" style={{ animationDirection: 'reverse' }} />
          )}
        </div>
      </div>

      <div className="absolute bottom-0 text-xs font-mono text-muted-foreground uppercase tracking-widest opacity-50">
        Telemetry Sync Active
      </div>
    </div>
  );
}
