import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AlertOctagon, ChevronRight, Loader2 } from "lucide-react";
import { apiFetch } from "../../lib/api";
import { cn } from "../../lib/utils";
import { relativeTime, titleCase } from "../../lib/format";
import type { IncidentSummary } from "../../lib/types";

const SEVERITY_LEFT: Record<string, string> = {
  CRITICAL: "border-l-status-critical",
  WARNING:  "border-l-status-warning",
  INFO:     "border-l-border",
};

const SEVERITY_BADGE: Record<string, string> = {
  CRITICAL: "bg-status-critical/12 text-status-critical",
  WARNING:  "bg-status-warning/12 text-status-warning",
  INFO:     "bg-secondary text-muted-foreground",
};

const STATUS_LABEL: Record<string, string> = {
  OPEN: "Open", ACKNOWLEDGED: "Acknowledged", CLOSED: "Closed",
};

/** Polls GET /incidents every 10s — no site-wide WS exists. */
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
    <div className="glass-card flex flex-col gap-0">
      {/* Header */}
      <div className="flex items-center justify-between p-5 border-b border-border/40">
        <div className="flex items-center gap-2.5">
          <AlertOctagon className="w-4 h-4 text-status-critical" />
          <h2 className="text-sm font-bold text-foreground tracking-wide uppercase">Incidents</h2>
        </div>
        {openCount > 0 && (
          <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-status-critical/12 text-status-critical intel-num">
            {openCount} open
          </span>
        )}
      </div>

      {loading ? (
        <div className="flex items-center gap-2 p-5 text-sm text-muted-foreground">
          <Loader2 className="w-4 h-4 animate-spin" /> Loading…
        </div>
      ) : error ? (
        <p className="p-5 text-sm text-status-critical">{error}</p>
      ) : incidents.length === 0 ? (
        <div className="p-8 text-center">
          <AlertOctagon className="w-8 h-8 text-muted-foreground/20 mx-auto mb-2" />
          <p className="text-sm text-muted-foreground">No incidents recorded.</p>
        </div>
      ) : (
        <div className="flex flex-col divide-y divide-border/40 max-h-[480px] overflow-y-auto intel-scroll">
          {incidents.map((inc, i) => (
            <Link
              key={inc.id}
              to={`/supervisor/incidents/${inc.id}`}
              className={cn(
                "flex items-center justify-between gap-3 px-4 py-3.5 border-l-2 hover:bg-secondary/30 transition-colors intel-rise",
                SEVERITY_LEFT[inc.severity] ?? SEVERITY_LEFT.INFO,
              )}
              style={{ animationDelay: `${Math.min(i, 8) * 30}ms` }}
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-mono text-sm font-bold text-foreground">{inc.machine_id}</span>
                  <span
                    className={cn(
                      "text-[10px] font-bold uppercase px-1.5 py-0.5 rounded",
                      SEVERITY_BADGE[inc.severity],
                    )}
                  >
                    {inc.severity}
                  </span>
                  {inc.escalated && (
                    <span className="text-[9px] uppercase font-bold px-1.5 py-0.5 rounded bg-status-critical/15 text-status-critical">
                      Escalated
                    </span>
                  )}
                </div>
                <div className="text-xs text-muted-foreground mt-1 truncate">
                  {titleCase(inc.category)} &middot; {STATUS_LABEL[inc.status]} &middot; {inc.event_count} events &middot; {relativeTime(inc.last_event_at)}
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
