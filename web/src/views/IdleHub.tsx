import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMachine } from '../store/MachineContext';
import { Coffee, SignalZero } from 'lucide-react';
import { EtaCard } from '../components/intel/EtaCard';
import { IdleAttributionCard } from '../components/intel/IdleAttributionCard';
import { LessonPlayer } from '../components/lessons/LessonPlayer';
import { AppLayout } from '../components/layout/AppLayout';
import { cn } from '../lib/utils';

export function IdleHub() {
  const { machineId, uiMode, status, eta, idleAttribution, lesson } = useMachine();
  const navigate = useNavigate();

  useEffect(() => {
    if (!machineId) navigate('/operator/prestart');
  }, [machineId, navigate]);

  useEffect(() => {
    if (uiMode === 'HUD') navigate('/operator/hud');
  }, [uiMode, navigate]);

  const isLive = status === 'CONNECTED';

  return (
    <AppLayout>
      <div className="min-h-full bg-[#080d1a] relative">

        {/* Subtle background glow */}
        <div
          className="absolute inset-0 pointer-events-none"
          style={{ background: 'radial-gradient(ellipse 80% 50% at 50% 20%, rgb(245 158 11 / 0.04) 0%, transparent 70%)' }}
        />

        <div className="relative z-10 p-8 max-w-5xl mx-auto">
          {/* Header */}
          <div className="flex items-center gap-3 mb-8">
            <div className="w-10 h-10 rounded-xl bg-status-warning/10 border border-status-warning/20 flex items-center justify-center">
              <Coffee className="w-5 h-5 text-status-warning" />
            </div>
            <div>
              <div className="data-label mb-0.5">Machine Idle</div>
              <h1 className="text-2xl font-black text-foreground tracking-tight">Idle Hub</h1>
            </div>
            <div className="ml-auto flex items-center gap-2">
              <span className="font-mono text-sm text-muted-foreground">{machineId ?? '—'}</span>
              <span className="state-badge state-idle">
                <span className="w-1.5 h-1.5 rounded-full bg-status-warning" />
                Idle
              </span>
            </div>
          </div>

          {/* Main cards */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-6">
            <EtaCard eta={eta} />
            <IdleAttributionCard machineId={machineId} latest={idleAttribution} />
          </div>

          {/* Micro-lesson card (Stage 4A-4C: real lesson content, generated
              from a real incident and grounded like incident explanations —
              quiz/effectiveness tracking are out of this batch's scope). */}
          <LessonPlayer livePush={lesson} />
        </div>

        {/* Connection status toast (non-blocking) */}
        {!isLive && (
          <div className={cn(
            'fixed bottom-5 right-5 glass-card px-4 py-2.5 flex items-center gap-2.5 text-sm border z-50',
            status === 'STALE'
              ? 'text-status-warning border-status-warning/30 bg-status-warning/5'
              : 'text-status-critical border-status-critical/30 bg-status-critical/5'
          )}>
            <SignalZero className="w-4 h-4" />
            <span>{status === 'STALE' ? 'Data may be stale' : 'Live connection lost'}</span>
            <div className={cn(
              'w-1.5 h-1.5 rounded-full animate-pulse',
              status === 'STALE' ? 'bg-status-warning' : 'bg-status-critical'
            )} />
          </div>
        )}
      </div>
    </AppLayout>
  );
}
