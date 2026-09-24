import { Clock, HelpCircle } from "lucide-react";
import { fmtMinutes, fmtPercent, titleCase } from "../../lib/format";
import { EtaFactorBars } from "./EtaFactorBars";
import type { EtaOrUnavailable } from "../../store/useMachineStream";

export function EtaCard({ eta }: { eta: EtaOrUnavailable | null }) {
  return (
    <div className="glass-card p-6 flex flex-col gap-5">
      <div className="flex items-center gap-3">
        <Clock className="w-4 h-4 text-primary" />
        <h2 className="text-sm font-bold text-foreground tracking-wide uppercase">Task ETA</h2>
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
              <div className="data-label mb-1.5">Remaining (P50)</div>
              <div className="intel-num text-4xl font-black tracking-tight text-foreground">
                {fmtMinutes(eta.remaining_p50_min ?? eta.baseline_p50_min)}
              </div>
              {eta.remaining_p10_min != null && eta.remaining_p90_min != null && (
                <div className="intel-num text-sm text-muted-foreground mt-1">
                  P10&nbsp;{fmtMinutes(eta.remaining_p10_min)}&nbsp;&ndash;&nbsp;P90&nbsp;{fmtMinutes(eta.remaining_p90_min)}
                </div>
              )}
            </div>
            {eta.progress != null && (
              <div className="text-right">
                <div className="data-label mb-1">Progress</div>
                <div className="intel-num text-2xl font-bold text-primary">{fmtPercent(eta.progress)}</div>
              </div>
            )}
          </div>

          {eta.progress != null && (
            <div>
              <div className="data-label mb-1.5">Block Completion</div>
              <div className="h-2 rounded-full bg-secondary overflow-hidden">
                <div
                  className="h-full bg-primary intel-bar-fill rounded-full"
                  style={{ width: `${Math.min(100, (eta.progress ?? 0) * 100)}%` }}
                />
              </div>
            </div>
          )}

          {eta.factors.length > 0 && (
            <div className="pt-3 border-t border-border/50">
              <div className="data-label mb-3">SHAP Contributing Delay Factors</div>
              <EtaFactorBars factors={eta.factors} />
              {eta.base_value_min != null && (
                <div className="flex justify-between text-[11px] text-muted-foreground mt-3 pt-2 border-t border-border/30">
                  <span>Base + factors</span>
                  <span className="intel-num">{eta.base_value_min.toFixed(1)}m base</span>
                </div>
              )}
            </div>
          )}

          <div className="flex items-center justify-between text-[10px] text-muted-foreground/50">
            <span>Times in sim minutes</span>
            {eta.model_version && (
              <span className="font-mono bg-secondary px-1.5 py-0.5 rounded truncate max-w-[50%]">
                eta-{eta.model_version}
              </span>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function EmptyState({ label }: { label: string }) {
  return (
    <div className="flex-1 flex flex-col items-center justify-center min-h-[160px] border-2 border-dashed border-border/50 rounded-lg bg-secondary/20 gap-3">
      <HelpCircle className="w-6 h-6 text-muted-foreground opacity-40" />
      <p
        className="text-sm text-muted-foreground text-center px-4"
        dangerouslySetInnerHTML={{ __html: label }}
      />
    </div>
  );
}
