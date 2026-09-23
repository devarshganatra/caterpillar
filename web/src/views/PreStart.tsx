import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { apiFetch } from '../lib/api';
import { useMachine } from '../store/MachineContext';
import { CheckCircle2, ChevronRight, AlertTriangle } from 'lucide-react';

export function PreStart() {
  const [tasks, setTasks] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  
  // Checklist State
  const [checks, setChecks] = useState({
    fluids: false,
    walkaround: false,
    radio: false,
  });

  const { setMachineId } = useMachine();
  const navigate = useNavigate();

  useEffect(() => {
    async function loadTasks() {
      try {
        const res = await apiFetch('/tasks/me/tasks');
        if (!res.ok) throw new Error('Failed to load tasks');
        const data = await res.json();
        setTasks(data);
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
  
  const handleStartShift = () => {
    if (!selectedTask || !allChecked) return;
    
    // Set the machine context so the WS connection starts
    setMachineId(selectedTask.machine_id);
    navigate('/operator/hud');
  };

  return (
    <div className="min-h-screen p-8 max-w-4xl mx-auto space-y-8">
      <div>
        <h1 className="text-3xl font-bold text-primary">Pre-Start Checklist</h1>
        <p className="text-muted-foreground mt-2">Select your assigned task and complete safety checks.</p>
      </div>

      {loading ? (
        <div className="glass-card p-8 flex justify-center text-muted-foreground">Loading tasks...</div>
      ) : error ? (
        <div className="glass-card p-8 border-destructive/50 bg-destructive/10 text-destructive flex items-center gap-3">
          <AlertTriangle /> {error}
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
          {/* Left Col: Tasks */}
          <div className="space-y-4">
            <h2 className="text-xl font-semibold">Today's Assignments</h2>
            {tasks.length === 0 ? (
              <p className="text-muted-foreground">No tasks assigned for today.</p>
            ) : (
              tasks.map(task => (
                <div 
                  key={task.id}
                  onClick={() => setSelectedTaskId(task.id)}
                  className={`glass-card p-4 cursor-pointer transition-colors ${selectedTaskId === task.id ? 'border-primary ring-1 ring-primary' : 'hover:border-primary/50'}`}
                >
                  <div className="flex justify-between items-start">
                    <div>
                      <div className="text-sm text-muted-foreground font-mono">{task.id}</div>
                      <div className="font-bold text-lg mt-1">Machine {task.machine_id}</div>
                      <div className="text-sm mt-1">Site {task.site_id} • Est. {task.est_duration_minutes}m</div>
                    </div>
                    {selectedTaskId === task.id && <CheckCircle2 className="text-primary" />}
                  </div>
                </div>
              ))
            )}
          </div>

          {/* Right Col: Checklist */}
          <div className="space-y-6">
            <h2 className="text-xl font-semibold">Safety Checks</h2>
            
            <div className={`glass-card p-6 space-y-4 transition-opacity ${!selectedTaskId ? 'opacity-50 pointer-events-none' : ''}`}>
              <label className="flex items-center gap-3 cursor-pointer">
                <input 
                  type="checkbox" 
                  checked={checks.walkaround} 
                  onChange={(e) => setChecks(prev => ({...prev, walkaround: e.target.checked}))}
                  className="w-5 h-5 rounded border-border bg-input text-primary focus:ring-primary"
                />
                <span className={checks.walkaround ? "text-muted-foreground line-through" : ""}>360° Walkaround completed</span>
              </label>
              <label className="flex items-center gap-3 cursor-pointer">
                <input 
                  type="checkbox" 
                  checked={checks.fluids} 
                  onChange={(e) => setChecks(prev => ({...prev, fluids: e.target.checked}))}
                  className="w-5 h-5 rounded border-border bg-input text-primary focus:ring-primary"
                />
                <span className={checks.fluids ? "text-muted-foreground line-through" : ""}>Fluid levels normal (fuel, oil, coolant)</span>
              </label>
              <label className="flex items-center gap-3 cursor-pointer">
                <input 
                  type="checkbox" 
                  checked={checks.radio} 
                  onChange={(e) => setChecks(prev => ({...prev, radio: e.target.checked}))}
                  className="w-5 h-5 rounded border-border bg-input text-primary focus:ring-primary"
                />
                <span className={checks.radio ? "text-muted-foreground line-through" : ""}>Radio comms check successful</span>
              </label>
            </div>

            <button
              onClick={handleStartShift}
              disabled={!selectedTaskId || !allChecked}
              className="w-full flex items-center justify-center gap-2 bg-primary text-primary-foreground font-bold py-4 rounded-lg hover:bg-primary/90 disabled:opacity-50 disabled:hover:bg-primary transition-colors text-lg"
            >
              Start Shift <ChevronRight />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
