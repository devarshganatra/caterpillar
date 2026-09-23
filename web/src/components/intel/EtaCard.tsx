import { Clock, HelpCircle } from "lucide-react";
import { fmtMinutes, fmtPercent, titleCase } from "../../lib/format";
import { EtaFactorBars } from "./EtaFactorBars";
import type { EtaOrUnavailable } from "../../store/useMachineStream";

export function EtaCard({ eta }: { eta: EtaOrUnavailable | null }) {
  return (
    <div className="glass-card p-6 flex flex-col gap-5">
      <div className="flex items-center gap-3 text-muted-foreground">
        <Clock className="w-5 h-5" />
        <h2 className="text-xl font-semibold text-foreground">Task ETA</h2>
      </div>

      {!eta ? (
        <EmptyState label="Waiting for live data&hellip;" />
      ) : eta.status === "UNAVAILABLE" ? (
        <EmptyState
          label={`ETA unavailable${eta.unavailable_reason ? ` — ${titleCase(eta.unavailable_reason)}` : ""}`}
        />
      ) : (
        <>
          <div className="flex items-end justify-between">
            <div>
              <div className="text-xs uppercase tracking-wider text-muted-foreground font-medium mb-1">
                Remaining (P50)
              </div>
              <div className="intel-num text-4xl font-bold tracking-tight">
                {fmtMinutes(eta.remaining_p50_min ?? eta.baseline_p50_min)}
              </div>
              {eta.remaining_p10_min != null && eta.remaining_p90_min != null && (
                <div className="intel-num text-sm text-muted-foreground mt-1">
                  {fmtMinutes(eta.remaining_p10_min)} &ndash; {fmtMinutes(eta.remaining_p90_min)} band
                </div>
              )}
            </div>
            {eta.progress != null && (
              <div className="text-right">
                <div className="text-xs uppercase tracking-wider text-muted-foreground font-medium mb-1">
                  Progress
                </div>
                <div className="intel-num text-2xl font-semibold">{fmtPercent(eta.progress)}</div>
              </div>
            )}
          </div>

          {eta.progress != null && (
            <div className="h-1.5 rounded-full bg-secondary overflow-hidden">
              <div
                className="h-full bg-primary intel-bar-fill rounded-full"
                style={{ width: `${Math.min(100, (eta.progress ?? 0) * 100)}%` }}
              />
            </div>
          )}

          {eta.factors.length > 0 && (
            <div className="pt-2 border-t border-border/50">
              <div className="text-xs uppercase tracking-wider text-muted-foreground font-medium mb-3">
                Contributing factors
              </div>
              <EtaFactorBars factors={eta.factors} />
              {eta.base_value_min != null && (
                <div className="flex justify-between text-xs text-muted-foreground mt-3 pt-2 border-t border-border/30">
                  <span>Base + factors</span>
                  <span className="intel-num">
                    {eta.base_value_min.toFixed(1)}m base
                  </span>
                </div>
              )}
            </div>
          )}

          <div className="flex items-center justify-between text-[11px] text-muted-foreground/70">
            <span>Times shown in simulated minutes</span>
            {eta.model_version && <span className="font-mono truncate max-w-[50%]">{eta.model_version}</span>}
          </div>
        </>
      )}
    </div>
  );
}

function EmptyState({ label }: { label: string }) {
  return (
    <div className="flex-1 flex flex-col items-center justify-center min-h-[160px] border-2 border-dashed border-border rounded-lg bg-background/30 gap-2">
      <HelpCircle className="w-6 h-6 text-muted-foreground opacity-50" />
      <p
        className="text-sm text-muted-foreground text-center px-4"
        dangerouslySetInnerHTML={{ __html: label }}
      />
    </div>
  );
}
