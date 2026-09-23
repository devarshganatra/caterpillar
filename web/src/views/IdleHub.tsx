import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMachine } from '../store/MachineContext';
import { Coffee, GraduationCap } from 'lucide-react';
import { EtaCard } from '../components/intel/EtaCard';
import { IdleAttributionCard } from '../components/intel/IdleAttributionCard';

export function IdleHub() {
  const { machineId, uiMode, status, eta, idleAttribution } = useMachine();
  const navigate = useNavigate();

  useEffect(() => {
    if (!machineId) {
      navigate('/operator/prestart');
    }
  }, [machineId, navigate]);

  useEffect(() => {
    if (uiMode === 'HUD') {
      navigate('/operator/hud');
    }
  }, [uiMode, navigate]);

  const isLive = status === 'CONNECTED';

  return (
    <div className="min-h-screen bg-zinc-950 p-8 flex flex-col items-center justify-center relative">

      {/* Background decoration */}
      <div className="absolute inset-0 flex items-center justify-center opacity-5 pointer-events-none">
        <Coffee className="w-96 h-96" />
      </div>

      <div className="z-10 text-center mb-12 intel-rise">
        <h1 className="text-4xl font-bold text-primary">Idle Hub</h1>
        <p className="text-muted-foreground mt-2 font-mono uppercase tracking-wider">
          Machine {machineId} &middot; Waiting for next task
        </p>
      </div>

      <div className="z-10 grid grid-cols-1 md:grid-cols-2 gap-8 w-full max-w-4xl">
        <EtaCard eta={eta} />
        <IdleAttributionCard machineId={machineId} latest={idleAttribution} />
      </div>

      <div className="z-10 w-full max-w-4xl mt-8">
        <div className="glass-card p-6 flex flex-col">
          <div className="flex items-center gap-3 mb-4 text-primary">
            <GraduationCap className="w-5 h-5" />
            <h2 className="text-xl font-semibold">Micro-Lesson</h2>
          </div>
          <div className="flex-1 flex flex-col items-center justify-center min-h-[120px] border-2 border-dashed border-border rounded-lg bg-background/30">
            <p className="text-muted-foreground font-medium">No pending lessons.</p>
          </div>
        </div>
      </div>

      {!isLive && (
        <div className="fixed bottom-4 right-4 glass-card px-4 py-2 text-status-critical border-status-critical flex items-center gap-2">
          <div className="w-2 h-2 rounded-full bg-status-critical animate-pulse" />
          Live connection lost
        </div>
      )}
    </div>
  );
}
