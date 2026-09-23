import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AlertOctagon, ChevronRight } from "lucide-react";
import { apiFetch } from "../../lib/api";
import { cn } from "../../lib/utils";
import { relativeTime, titleCase } from "../../lib/format";
import type { IncidentSummary } from "../../lib/types";

const SEVERITY_STYLE: Record<string, string> = {
  CRITICAL: "border-l-status-critical",
  WARNING: "border-l-status-warning",
  INFO: "border-l-status-offline",
};

const STATUS_LABEL: Record<string, string> = { OPEN: "Open", ACKNOWLEDGED: "Acknowledged", CLOSED: "Closed" };

/**
 * Polls GET /incidents every 10s. There is no site-wide WebSocket in this
 * system (only per-machine /ws/stream/{id}), so a poll is the correct
 * mechanism here rather than a workaround — see contracts/openapi.md
 * "Not yet implemented".
 */
export function IncidentList({ siteId }: { siteId: string | null }) {
  const [incidents, setIncidents] = useState<IncidentSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!siteId) return;
    try {
      const res = await apiFetch(`/incidents?site_id=${encodeURIComponent(siteId)}&limit=50`);
      if (!res.ok) throw new Error("Failed to load incidents");
      const data = await res.json();
      setIncidents(data.items);
      setError(null);
    } catch (e: any) {
      setError(e.message ?? "Failed to load incidents");
    } finally {
      setLoading(false);
    }
  }, [siteId]);

  useEffect(() => {
    load();
    const interval = window.setInterval(load, 10000);
    return () => window.clearInterval(interval);
  }, [load]);

  if (!siteId) return null;

  const openCount = incidents.filter((i) => i.status !== "CLOSED").length;

  return (
    <div className="glass-card p-6 flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3 text-muted-foreground">
          <AlertOctagon className="w-5 h-5" />
          <h2 className="text-xl font-semibold text-foreground">Incidents</h2>
        </div>
        {openCount > 0 && (
          <span className="px-2 py-0.5 rounded-full text-xs font-bold bg-status-critical/15 text-status-critical intel-num">
            {openCount} open
          </span>
        )}
      </div>

      {loading ? (
        <p className="text-sm text-muted-foreground">Loading&hellip;</p>
      ) : error ? (
        <p className="text-sm text-status-critical">{error}</p>
      ) : incidents.length === 0 ? (
        <p className="text-sm text-muted-foreground">No incidents recorded for this site.</p>
      ) : (
        <div className="flex flex-col divide-y divide-border/50 -mx-2 max-h-[440px] overflow-y-auto intel-scroll">
          {incidents.map((inc, i) => (
            <Link
              key={inc.id}
              to={`/supervisor/incidents/${inc.id}`}
              className={cn(
                "flex items-center justify-between gap-3 px-3 py-3 border-l-2 hover:bg-secondary/40 transition-colors rounded-r-sm intel-rise",
                SEVERITY_STYLE[inc.severity] ?? SEVERITY_STYLE.INFO,
              )}
              style={{ animationDelay: `${Math.min(i, 8) * 30}ms` }}
            >
              <div className="min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-mono text-sm font-semibold text-foreground">{inc.machine_id}</span>
                  <span className="text-xs text-muted-foreground uppercase tracking-wide">
                    {titleCase(inc.category)}
                  </span>
                  {inc.escalated && (
                    <span className="text-[10px] uppercase font-bold px-1.5 py-0.5 rounded bg-status-critical/15 text-status-critical">
                      Escalated
                    </span>
                  )}
                </div>
                <div className="text-xs text-muted-foreground mt-1">
                  {STATUS_LABEL[inc.status]} &middot; {inc.event_count} events &middot; {relativeTime(inc.last_event_at)}
                </div>
              </div>
              <ChevronRight className="w-4 h-4 text-muted-foreground shrink-0" />
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
