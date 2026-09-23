import { useState, useEffect } from 'react';
import { apiFetch } from '../lib/api';
import { MachineCard } from '../components/MachineCard';
import { AlertTriangle, MapPin } from 'lucide-react';

export function Supervisor() {
  const [sites, setSites] = useState<string[]>([]);
  const [selectedSite, setSelectedSite] = useState<string | null>(null);
  const [tasks, setTasks] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function loadSites() {
      try {
        const res = await apiFetch('/auth/me');
        if (!res.ok) throw new Error('Failed to fetch user profile');
        const data = await res.json();
        setSites(data.site_ids);
        if (data.site_ids.length > 0) {
          setSelectedSite(data.site_ids[0]);
        }
      } catch (err: any) {
        setError(err.message);
      }
    }
    loadSites();
  }, []);

  useEffect(() => {
    async function loadTasks() {
      if (!selectedSite) return;
      setLoading(true);
      setError(null);
      try {
        const res = await apiFetch(`/tasks/sites/${selectedSite}/tasks`);
        if (!res.ok) throw new Error('Failed to load tasks for site');
        const data = await res.json();
        setTasks(data);
      } catch (err: any) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    }
    loadTasks();
  }, [selectedSite]);

  // Group tasks by machine (in a real app, one machine might have multiple planned tasks, but we just want to track machines)
  // For MVP, we'll assume one active task per machine or just unique machines.
  const uniqueMachines = Array.from(new Set(tasks.map(t => t.machine_id)));
  
  return (
    <div className="min-h-screen p-8 max-w-6xl mx-auto space-y-8">
      <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
        <div>
          <h1 className="text-3xl font-bold text-primary">Fleet Supervisor</h1>
          <p className="text-muted-foreground mt-2">Live monitoring of authorized sites.</p>
        </div>
        
        {sites.length > 0 && (
          <div className="flex items-center gap-2 glass-card px-4 py-2">
            <MapPin className="text-primary w-4 h-4" />
            <select 
              className="bg-transparent text-foreground focus:outline-none cursor-pointer"
              value={selectedSite || ''}
              onChange={(e) => setSelectedSite(e.target.value)}
            >
              {sites.map(site => (
                <option key={site} value={site} className="bg-zinc-900">{site}</option>
              ))}
            </select>
          </div>
        )}
      </div>

      {error ? (
        <div className="glass-card p-8 border-destructive/50 bg-destructive/10 text-destructive flex items-center gap-3">
          <AlertTriangle /> {error}
        </div>
      ) : loading ? (
        <div className="glass-card p-8 flex justify-center text-muted-foreground">Loading fleet data...</div>
      ) : uniqueMachines.length === 0 ? (
        <div className="glass-card p-12 text-center flex flex-col items-center">
          <MapPin className="w-12 h-12 text-muted-foreground opacity-50 mb-4" />
          <h3 className="text-xl font-semibold">No Machines Active</h3>
          <p className="text-muted-foreground mt-2">There are no tasks assigned at {selectedSite} today.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6">
          {uniqueMachines.map(machineId => {
            const mTask = tasks.find(t => t.machine_id === machineId);
            return <MachineCard key={machineId} machineId={machineId} initialTask={mTask} />;
          })}
        </div>
      )}
    </div>
  );
}
