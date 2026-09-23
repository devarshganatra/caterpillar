import { Clock, TrendingUp, HelpCircle } from "lucide-react";
import { cn } from "../../lib/utils";
import { fmtMinutes, titleCase } from "../../lib/format";
import type { EtaOrUnavailable } from "../../store/useMachineStream";

/** Compact HUD chip: model-backed ETA remaining, with its P10–P90 band. */
export function EtaChip({ eta, compact = false }: { eta: EtaOrUnavailable | null; compact?: boolean }) {
  const pad = compact ? "px-2.5 py-1" : "px-4 py-2";

  if (!eta) {
    return (
      <div className={cn("glass-card flex items-center gap-2 opacity-60", pad)}>
        <Clock className="w-3.5 h-3.5 text-muted-foreground" />
        <span className="text-xs text-muted-foreground">ETA connecting&hellip;</span>
      </div>
    );
  }

  if (eta.status === "UNAVAILABLE") {
    return (
      <div className={cn("glass-card flex items-center gap-2", pad)}>
        <HelpCircle className="w-3.5 h-3.5 text-muted-foreground" />
        <span className="text-xs text-muted-foreground">
          {compact ? "ETA n/a" : "ETA unavailable"}
          {!compact && eta.unavailable_reason && (
            <span className="opacity-70"> &middot; {titleCase(eta.unavailable_reason)}</span>
          )}
        </span>
      </div>
    );
  }

  const p50 = eta.remaining_p50_min ?? eta.baseline_p50_min;
  const p10 = eta.remaining_p10_min ?? eta.baseline_p10_min;
  const p90 = eta.remaining_p90_min ?? eta.baseline_p90_min;
  const slipping = (eta.slip_pct ?? 0) >= 0.1;

  return (
    <div
      className={cn(
        "glass-card flex items-center gap-2 transition-colors",
        pad,
        compact ? "gap-2" : "gap-3",
        slipping && "border-status-warning/70",
      )}
    >
      <Clock className={cn("shrink-0", compact ? "w-3.5 h-3.5" : "w-4 h-4", slipping ? "text-status-warning" : "text-primary")} />
      <div className="flex items-baseline gap-1.5 leading-none">
        <span className={cn("intel-num font-bold", compact ? "text-sm" : "text-lg")}>{fmtMinutes(p50)}</span>
        {!compact && p10 != null && p90 != null && (
          <span className="intel-num text-xs text-muted-foreground">
            [{fmtMinutes(p10)}&ndash;{fmtMinutes(p90)}]
          </span>
        )}
      </div>
      {slipping && (
        <span className="flex items-center gap-1 text-[10px] font-bold uppercase tracking-wide text-status-warning">
          <TrendingUp className="w-3 h-3" /> {compact ? "" : "slipping"}
        </span>
      )}
    </div>
  );
}
