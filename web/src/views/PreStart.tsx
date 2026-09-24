import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { apiFetch } from '../lib/api';
import { useMachine } from '../store/MachineContext';
import { CheckCircle2, Circle, ChevronRight, AlertTriangle, Loader2, Wrench, MapPin, Clock } from 'lucide-react';
import { AppLayout } from '../components/layout/AppLayout';
import { cn } from '../lib/utils';

const CHECKS = [
  { key: 'walkaround', label: '360° Walkaround completed', description: 'Inspect tracks, bucket, cab, and undercarriage' },
  { key: 'fluids',     label: 'Fluid levels normal',      description: 'Fuel, oil, coolant, hydraulic fluid' },
  { key: 'radio',      label: 'Radio comms check',        description: 'Contact dispatcher and confirm signal strength' },
] as const;

type CheckKey = typeof CHECKS[number]['key'];

export function PreStart() {
  const [tasks, setTasks] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [checks, setChecks] = useState<Record<CheckKey, boolean>>({
    fluids: false, walkaround: false, radio: false,
  });

  const { setMachineId } = useMachine();
  const navigate = useNavigate();

  useEffect(() => {
    async function loadTasks() {
      try {
        const res = await apiFetch('/tasks/me/tasks');
        if (!res.ok) throw new Error('Failed to load tasks');
        setTasks(await res.json());
      } catch (err: any) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    }
    loadTasks();
  }, []);

  const selectedTask = tasks.find(t => t.id === selectedTaskId);
  const allChecked = Object.values(checks).every(Boolean);
  const checksCompleted = Object.values(checks).filter(Boolean).length;

  const handleStartShift = () => {
    if (!selectedTask || !allChecked) return;
    setMachineId(selectedTask.machine_id);
    navigate('/operator/hud');
  };

  return (
    <AppLayout>
      <div className="p-8 max-w-5xl mx-auto">
        {/* Header */}
        <div className="mb-8">
          <div className="data-label mb-2">Morning Pre-Shift</div>
          <h1 className="text-3xl font-black text-foreground tracking-tight">Pre-Start Checklist</h1>
          <p className="text-muted-foreground mt-1.5 text-sm">
            Select your assigned task and complete all safety checks before starting.
          </p>
        </div>

        {loading ? (
          <div className="glass-card p-12 flex flex-col items-center gap-3 text-muted-foreground">
            <Loader2 className="w-6 h-6 animate-spin" />
            <span className="text-sm">Loading assignments…</span>
          </div>
        ) : error ? (
          <div className="glass-card p-8 border border-status-critical/30 bg-status-critical/5 text-status-critical flex items-center gap-3">
            <AlertTriangle className="shrink-0" />
            <div>
              <div className="font-semibold">Failed to load tasks</div>
              <div className="text-sm opacity-80 mt-0.5">{error}</div>
            </div>
          </div>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-[1fr_380px] gap-8 items-start">

            {/* Left: Task assignments */}
            <div>
              <div className="flex items-center gap-2 mb-4">
                <Wrench className="w-4 h-4 text-primary" />
                <h2 className="font-semibold text-foreground">Today's Assignments</h2>
                <span className="ml-auto text-xs text-muted-foreground">{tasks.length} task{tasks.length !== 1 ? 's' : ''}</span>
              </div>

              {tasks.length === 0 ? (
                <div className="glass-card p-10 text-center text-muted-foreground">
                  <Wrench className="w-8 h-8 mx-auto mb-3 opacity-30" />
                  <p className="font-medium">No tasks assigned for today.</p>
                  <p className="text-sm mt-1 opacity-70">Contact your supervisor to confirm your schedule.</p>
                </div>
              ) : (
                <div className="space-y-3">
                  {tasks.map(task => {
                    const isSelected = selectedTaskId === task.id;
                    return (
                      <div
                        key={task.id}
                        onClick={() => setSelectedTaskId(task.id)}
                        className={cn(
                          'glass-card p-5 cursor-pointer transition-all duration-150',
                          isSelected
                            ? 'border-primary/60 bg-primary/5 shadow-[0_0_0_1px_rgb(255_203_5_/_0.3)]'
                            : 'hover:border-border hover:bg-secondary/30'
                        )}
                      >
                        <div className="flex justify-between items-start">
                          <div className="min-w-0 flex-1">
                            <div className="flex items-center gap-2 mb-1.5">
                              <span className="font-mono text-xs text-muted-foreground">{task.id}</span>
                              {isSelected && (
                                <span className="text-[10px] font-bold uppercase tracking-wider text-primary bg-primary/10 px-2 py-0.5 rounded-full">
                                  Selected
                                </span>
                              )}
                            </div>
                            <div className="font-bold text-lg text-foreground">Machine {task.machine_id}</div>
                            <div className="flex items-center gap-3 mt-2 text-sm text-muted-foreground">
                              <span className="flex items-center gap-1">
                                <MapPin className="w-3.5 h-3.5" /> Site {task.site_id}
                              </span>
                              {task.est_duration_minutes && (
                                <span className="flex items-center gap-1">
                                  <Clock className="w-3.5 h-3.5" /> Est. {task.est_duration_minutes}m
                                </span>
                              )}
                            </div>
                          </div>
                          <div className={cn(
                            'w-6 h-6 rounded-full border-2 flex items-center justify-center shrink-0 ml-4 mt-1 transition-all',
                            isSelected ? 'border-primary bg-primary' : 'border-border'
                          )}>
                            {isSelected && <CheckCircle2 className="w-4 h-4 text-primary-foreground" />}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>

            {/* Right: Checklist + Start */}
            <div className="space-y-5">
              <div>
                <div className="flex items-center gap-2 mb-4">
                  <CheckCircle2 className="w-4 h-4 text-primary" />
                  <h2 className="font-semibold text-foreground">Safety Checks</h2>
                  <span className="ml-auto text-xs text-muted-foreground">{checksCompleted}/{CHECKS.length} completed</span>
                </div>

                {/* Progress bar */}
                <div className="h-1 bg-secondary rounded-full mb-5 overflow-hidden">
                  <div
                    className="h-full bg-primary rounded-full intel-bar-fill"
                    style={{ width: `${(checksCompleted / CHECKS.length) * 100}%` }}
                  />
                </div>

                <div
                  className={cn(
                    'glass-card divide-y divide-border/50 transition-opacity',
                    !selectedTaskId && 'opacity-40 pointer-events-none'
                  )}
                >
                  {CHECKS.map(({ key, label, description }) => {
                    const checked = checks[key];
                    return (
                      <label
                        key={key}
                        className={cn(
                          'flex items-start gap-4 p-4 cursor-pointer group transition-colors',
                          checked ? 'bg-status-normal/5' : 'hover:bg-secondary/20'
                        )}
                      >
                        <div className="mt-0.5 shrink-0">
                          {checked
                            ? <CheckCircle2 className="w-5 h-5 text-status-normal" />
                            : <Circle className="w-5 h-5 text-border group-hover:text-muted-foreground transition-colors" />
                          }
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className={cn('text-sm font-medium transition-colors', checked && 'text-muted-foreground line-through')}>{label}</div>
                          <div className="text-xs text-muted-foreground/70 mt-0.5">{description}</div>
                        </div>
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={(e) => setChecks(prev => ({ ...prev, [key]: e.target.checked }))}
                          className="sr-only"
                        />
                      </label>
                    );
                  })}
                </div>

                {!selectedTaskId && (
                  <p className="text-xs text-muted-foreground text-center mt-3">
                    Select a task above to enable checks
                  </p>
                )}
              </div>

              <button
                onClick={handleStartShift}
                disabled={!selectedTaskId || !allChecked}
                className={cn(
                  'w-full flex items-center justify-center gap-2.5 font-bold py-4 rounded-lg transition-all text-sm tracking-wide',
                  selectedTaskId && allChecked
                    ? 'bg-primary text-primary-foreground hover:bg-primary/90 shadow-[0_0_24px_rgb(255_203_5_/_0.2)]'
                    : 'bg-secondary text-muted-foreground cursor-not-allowed opacity-50'
                )}
              >
                Start Shift
                <ChevronRight className="w-4 h-4" />
              </button>

              {selectedTask && (
                <div className="glass-card p-3 text-xs text-muted-foreground flex items-center gap-2">
                  <span className="text-primary font-mono">{selectedTask.machine_id}</span>
                  <span>·</span>
                  <span>Site {selectedTask.site_id}</span>
                  <span>·</span>
                  <span className="font-mono">{selectedTask.id}</span>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </AppLayout>
  );
}
