import { useEffect, useState } from "react";
import { PieChart, AlertTriangle } from "lucide-react";
import { apiFetch } from "../../lib/api";
import { cn } from "../../lib/utils";
import { fmtSeconds } from "../../lib/format";
import type { IdleAttribution, IdleCause, WindowsResponse } from "../../lib/types";

const CAUSE_ORDER: IdleCause[] = ["OPERATOR", "SITE", "WEATHER", "MACHINE", "PLANNED"];

// Tailwind's scanner needs literal class strings — a `${x}/15` template
// concatenation is invisible to it and silently renders unstyled, so each
// variant (dot / bar / badge) is spelled out per cause rather than composed.
const CAUSE_STYLE: Record<IdleCause, { dot: string; bar: string; badge: string; label: string }> = {
  OPERATOR: { dot: "bg-primary", bar: "bg-primary", badge: "bg-primary/15 text-primary", label: "Operator" },
  SITE: { dot: "bg-status-warning", bar: "bg-status-warning", badge: "bg-status-warning/15 text-status-warning", label: "Site (no truck)" },
  WEATHER: { dot: "bg-sky-400", bar: "bg-sky-400", badge: "bg-sky-400/15 text-sky-400", label: "Weather" },
  MACHINE: { dot: "bg-status-critical", bar: "bg-status-critical", badge: "bg-status-critical/15 text-status-critical", label: "Machine fault" },
  PLANNED: { dot: "bg-status-offline", bar: "bg-status-offline", badge: "bg-status-offline/15 text-status-offline", label: "Planned break" },
};

export function IdleAttributionCard({
  machineId,
  latest,
}: {
  machineId: string | null;
  latest: IdleAttribution | null;
}) {
  const [windows, setWindows] = useState<WindowsResponse | null>(null);

  useEffect(() => {
    if (!machineId) return;
    let cancelled = false;

    async function load() {
      try {
        const res = await apiFetch(`/machines/${machineId}/windows?limit=20`);
        if (res.ok && !cancelled) setWindows(await res.json());
      } catch {
        // transient fetch failure — the poll will retry
      }
    }
    load();
    const interval = window.setInterval(load, 15000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [machineId]);

  const totals = windows?.totals_s ?? {};
  const totalSeconds = CAUSE_ORDER.reduce((sum, c) => sum + (totals[c] ?? 0), 0);

  return (
    <div className="glass-card p-6 flex flex-col gap-5">
      <div className="flex items-center gap-3 text-muted-foreground">
        <PieChart className="w-5 h-5" />
        <h2 className="text-xl font-semibold text-foreground">Idle Attribution</h2>
      </div>

      {totalSeconds === 0 ? (
        <div className="flex-1 flex items-center justify-center min-h-[140px] border-2 border-dashed border-border rounded-lg bg-background/30">
          <p className="text-sm text-muted-foreground">No idle time recorded yet in recent windows.</p>
        </div>
      ) : (
        <>
          <div className="flex h-3 rounded-full overflow-hidden bg-secondary">
            {CAUSE_ORDER.map((cause) => {
              const seconds = totals[cause] ?? 0;
              if (seconds <= 0) return null;
              const pct = (seconds / totalSeconds) * 100;
              return (
                <div
                  key={cause}
                  className={cn(CAUSE_STYLE[cause].bar, "intel-bar-fill h-full first:rounded-l-full last:rounded-r-full")}
                  style={{ width: `${pct}%` }}
                  title={`${CAUSE_STYLE[cause].label}: ${fmtSeconds(seconds)}`}
                />
              );
            })}
          </div>

          <div className="grid grid-cols-2 gap-x-4 gap-y-2">
            {CAUSE_ORDER.map((cause) => (
              <div key={cause} className="flex items-center justify-between text-sm">
                <span className="flex items-center gap-2 text-muted-foreground">
                  <span className={cn("w-2 h-2 rounded-full shrink-0", CAUSE_STYLE[cause].dot)} />
                  {CAUSE_STYLE[cause].label}
                </span>
                <span className="intel-num font-medium">{fmtSeconds(totals[cause] ?? 0)}</span>
              </div>
            ))}
          </div>
        </>
      )}

      {latest?.primary_cause && (
        <div className="pt-4 border-t border-border/50 flex items-center gap-2 text-sm">
          <span className="text-muted-foreground">Latest window:</span>
          <span
            className={cn(
              "px-2 py-0.5 rounded text-xs font-semibold uppercase tracking-wide",
              CAUSE_STYLE[latest.primary_cause].badge,
            )}
          >
            {CAUSE_STYLE[latest.primary_cause].label}
          </span>
        </div>
      )}

      {latest?.operator_deviation_flag && (
        <div className="flex items-start gap-2 text-sm p-3 rounded-md bg-status-warning/10 border border-status-warning/30 text-status-warning intel-rise">
          <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
          <span>
            Idle time above expected for this context
            {latest.deviation_ratio != null && ` (×${latest.deviation_ratio.toFixed(1)})`}
          </span>
        </div>
      )}
    </div>
  );
}
