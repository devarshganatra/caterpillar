import { Activity, CircleSlash } from "lucide-react";
import { cn } from "../../lib/utils";
import type { AnomalyResult } from "../../lib/types";

const METHOD_LABEL: Record<string, string> = {
  IFOREST: "Isolation Forest",
  ROBUST_Z: "Robust-Z",
  SKIPPED: "Not applicable",
  UNAVAILABLE: "Unavailable",
};

/** Compact badge for a fleet card; expands to drivers when `expanded`. */
export function AnomalyBadge({ anomaly, expanded = false }: { anomaly: AnomalyResult | null; expanded?: boolean }) {
  if (!anomaly || anomaly.method === "SKIPPED" || anomaly.method === "UNAVAILABLE") {
    if (!expanded) return null;
    return (
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <CircleSlash className="w-3.5 h-3.5" />
        {anomaly ? METHOD_LABEL[anomaly.method] : "No data yet"}
      </div>
    );
  }

  if (!anomaly.is_anomalous) {
    if (!expanded) return null;
    return (
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <Activity className="w-3.5 h-3.5 text-status-normal" />
        Normal operating pattern
        {anomaly.score != null && <span className="intel-num opacity-70">({anomaly.score.toFixed(2)})</span>}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2 intel-rise">
      <div className="flex items-center gap-2">
        <span className="flex items-center gap-1.5 px-2 py-0.5 rounded text-xs font-semibold uppercase tracking-wide bg-status-warning/15 text-status-warning">
          <Activity className="w-3 h-3" />
          Unusual pattern
        </span>
        <span className="text-xs text-muted-foreground">
          {METHOD_LABEL[anomaly.method] ?? anomaly.method}
          {anomaly.score != null && <span className="intel-num"> &middot; {anomaly.score.toFixed(2)}</span>}
        </span>
      </div>
      {expanded && anomaly.drivers.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {anomaly.drivers.slice(0, 3).map((d) => (
            <span
              key={d.feature}
              className={cn(
                "intel-num px-2 py-0.5 rounded-sm text-[11px] bg-secondary text-muted-foreground",
              )}
              title={`raw value ${d.value}`}
            >
              {d.feature} {d.robust_z >= 0 ? "+" : ""}
              {d.robust_z.toFixed(1)}&sigma;
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
