import { cn } from "../../lib/utils";
import { fmtSignedMinutes } from "../../lib/format";
import type { EtaFactor } from "../../lib/types";

/**
 * Diverging horizontal bars, one per SHAP factor group, centered on a zero
 * line so the sign is legible at a glance without a legend. Orange = adds
 * time, green = saves time — reusing the app's existing status palette
 * rather than introducing a third accent color.
 */
export function EtaFactorBars({ factors }: { factors: EtaFactor[] }) {
  if (!factors.length) {
    return <p className="text-sm text-muted-foreground italic">No factor breakdown available.</p>;
  }

  const maxAbs = Math.max(...factors.map((f) => Math.abs(f.minutes)), 0.5);

  return (
    <div className="space-y-2.5">
      {factors.map((f, i) => {
        const halfWidthPct = (Math.abs(f.minutes) / maxAbs) * 50;
        const positive = f.minutes >= 0;
        return (
          <div
            key={f.group}
            className="grid grid-cols-[92px_1fr_58px] items-center gap-3 text-sm intel-rise"
            style={{ animationDelay: `${i * 35}ms` }}
          >
            <span className="text-muted-foreground truncate" title={f.top_feature}>
              {f.group}
            </span>
            <div className="relative h-4">
              <div className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-border" />
              <div
                className={cn(
                  "absolute inset-y-0 rounded-[2px] intel-bar-fill",
                  positive ? "left-1/2 bg-status-warning/80" : "right-1/2 bg-status-normal/80",
                )}
                style={{ width: `${halfWidthPct}%` }}
              />
            </div>
            <span
              className={cn(
                "intel-num text-right font-semibold tabular-nums",
                positive ? "text-status-warning" : "text-status-normal",
              )}
            >
              {fmtSignedMinutes(f.minutes)}
            </span>
          </div>
        );
      })}
    </div>
  );
}
