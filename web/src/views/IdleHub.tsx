import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMachine } from '../store/MachineContext';
import { Coffee, GraduationCap, PieChart } from 'lucide-react';


export function IdleHub() {
  const { machineId, currentState, status } = useMachine();
  const navigate = useNavigate();

  useEffect(() => {
    if (!machineId) {
      navigate('/operator/prestart');
    }
  }, [machineId, navigate]);

  useEffect(() => {
    // Navigate back to HUD if machine is no longer in IDLE_HUB
    if (currentState && currentState !== 'IDLE_HUB') {
      navigate('/operator/hud');
    }
  }, [currentState, navigate]);

  const isLive = status === 'CONNECTED';

  return (
    <div className="min-h-screen bg-zinc-950 p-8 flex flex-col items-center justify-center relative">
      
      {/* Background decoration */}
      <div className="absolute inset-0 flex items-center justify-center opacity-5 pointer-events-none">
        <Coffee className="w-96 h-96" />
      </div>

      <div className="z-10 text-center mb-12 animate-in fade-in slide-in-from-bottom-4">
        <h1 className="text-4xl font-bold text-primary">Idle Hub</h1>
        <p className="text-muted-foreground mt-2 font-mono uppercase tracking-wider">
          Machine {machineId} • Waiting for next phase
        </p>
      </div>

      <div className="z-10 grid grid-cols-1 md:grid-cols-2 gap-8 w-full max-w-4xl">
        
        {/* Idle Attribution Stub */}
        <div className="glass-card p-6 flex flex-col">
          <div className="flex items-center gap-3 mb-4 text-muted-foreground">
            <PieChart />
            <h2 className="text-xl font-semibold">Idle Attribution</h2>
          </div>
          
          <div className="flex-1 flex flex-col items-center justify-center min-h-[200px] border-2 border-dashed border-border rounded-lg bg-background/30">
            <p className="text-muted-foreground font-medium">Awaiting data...</p>
            <p className="text-xs text-muted-foreground mt-1 opacity-70">(ML Analytics Backend Not Yet Available)</p>
          </div>
        </div>

        {/* Micro-lesson Stub */}
        <div className="glass-card p-6 flex flex-col">
          <div className="flex items-center gap-3 mb-4 text-primary">
            <GraduationCap />
            <h2 className="text-xl font-semibold">Micro-Lesson</h2>
          </div>
          
          <div className="flex-1 flex flex-col items-center justify-center min-h-[200px] border-2 border-dashed border-border rounded-lg bg-background/30">
            <p className="text-muted-foreground font-medium">No pending lessons.</p>
            <p className="text-xs text-muted-foreground mt-1 opacity-70">(Gemini Insights Backend Not Yet Available)</p>
          </div>
        </div>

      </div>

      {!isLive && (
        <div className="fixed bottom-4 right-4 glass-card px-4 py-2 text-destructive border-destructive flex items-center gap-2">
          <div className="w-2 h-2 rounded-full bg-destructive animate-pulse" />
          Live connection lost
        </div>
      )}
    </div>
  );
}
