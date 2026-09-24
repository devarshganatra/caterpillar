import { useState, useEffect } from 'react';
import { apiFetch } from '../lib/api';
import { MachineCard } from '../components/MachineCard';
import { IncidentList } from '../components/incidents/IncidentList';
import { AlertTriangle, MapPin, Loader2, Users } from 'lucide-react';
import { AppLayout } from '../components/layout/AppLayout';

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
        if (data.site_ids.length > 0) setSelectedSite(data.site_ids[0]);
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
        setTasks(await res.json());
      } catch (err: any) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    }
    loadTasks();
  }, [selectedSite]);

  const uniqueMachines = Array.from(new Set(tasks.map(t => t.machine_id)));

  return (
    <AppLayout>
      <div className="p-8">
        {/* Header */}
        <div className="flex items-center justify-between mb-8">
          <div>
            <div className="data-label mb-1.5">Live Monitoring</div>
            <h1 className="text-3xl font-black text-foreground tracking-tight">Fleet Supervisor</h1>
            <p className="text-muted-foreground text-sm mt-1">
              Authorized sites · Real-time machine telemetry
            </p>
          </div>

          {sites.length > 0 && (
            <div className="flex items-center gap-2 glass-card px-3 py-2">
              <MapPin className="w-4 h-4 text-primary" />
              <select
                className="bg-transparent text-foreground text-sm focus:outline-none cursor-pointer"
                value={selectedSite || ''}
                onChange={(e) => setSelectedSite(e.target.value)}
              >
                {sites.map(site => (
                  <option key={site} value={site} className="bg-[#0f1729]">{site}</option>
                ))}
              </select>
            </div>
          )}
        </div>

        {error ? (
          <div className="glass-card p-6 border border-status-critical/30 bg-status-critical/5 text-status-critical flex items-center gap-3">
            <AlertTriangle className="shrink-0" />
            <div>
              <div className="font-semibold">Load failed</div>
              <div className="text-sm opacity-80 mt-0.5">{error}</div>
            </div>
          </div>
        ) : (
          <div className="grid grid-cols-1 xl:grid-cols-[1fr_340px] gap-8 items-start">
            {/* Machine grid */}
            <div>
              {loading ? (
                <div className="glass-card p-12 flex flex-col items-center gap-3 text-muted-foreground">
                  <Loader2 className="w-6 h-6 animate-spin" />
                  <span className="text-sm">Loading fleet data…</span>
                </div>
              ) : uniqueMachines.length === 0 ? (
                <div className="glass-card p-16 text-center">
                  <Users className="w-10 h-10 text-muted-foreground/30 mx-auto mb-4" />
                  <h3 className="text-lg font-semibold text-foreground">No Machines Active</h3>
                  <p className="text-sm text-muted-foreground mt-2">
                    No tasks assigned at {selectedSite} today.
                  </p>
                </div>
              ) : (
                <>
                  <div className="flex items-center gap-2 mb-4">
                    <div className="w-1.5 h-1.5 rounded-full bg-status-normal intel-live-dot" />
                    <span className="text-sm text-muted-foreground">
                      {uniqueMachines.length} machine{uniqueMachines.length !== 1 ? 's' : ''} on {selectedSite}
                    </span>
                  </div>
                  <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
                    {uniqueMachines.map(machineId => {
                      const mTask = tasks.find(t => t.machine_id === machineId);
                      return <MachineCard key={machineId} machineId={machineId} initialTask={mTask} />;
                    })}
                  </div>
                </>
              )}
            </div>

            {/* Incident panel */}
            <div className="space-y-4">
              <div className="data-label mb-1">Open Incidents</div>
              <IncidentList siteId={selectedSite} />
            </div>
          </div>
        )}
      </div>
    </AppLayout>
  );
}
