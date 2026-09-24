import { cn } from "../../lib/utils";
import { fmtSignedMinutes } from "../../lib/format";
import type { EtaFactor } from "../../lib/types";

/**
 * Diverging horizontal bars: amber/orange = adds time (delay), green = saves time (gain).
 * Signed value in red/green on the right. Zero line centered.
 */
export function EtaFactorBars({ factors }: { factors: EtaFactor[] }) {
  if (!factors.length) {
    return <p className="text-sm text-muted-foreground italic">No factor breakdown available.</p>;
  }

  const maxAbs = Math.max(...factors.map((f) => Math.abs(f.minutes)), 0.5);

  return (
    <div className="space-y-2">
      {factors.map((f, i) => {
        const halfWidthPct = (Math.abs(f.minutes) / maxAbs) * 50;
        const positive = f.minutes >= 0;
        return (
          <div
            key={f.group}
            className="grid grid-cols-[100px_1fr_56px] items-center gap-2 intel-rise"
            style={{ animationDelay: `${i * 35}ms` }}
          >
            <span className="text-xs text-muted-foreground truncate" title={String(f.top_feature ?? f.group)}>
              {f.group}
            </span>
            <div className="relative h-3.5">
              {/* Zero line */}
              <div className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-border" />
              <div
                className={cn(
                  "absolute inset-y-0 rounded-[2px] intel-bar-fill",
                  positive
                    ? "left-1/2 bg-status-warning/70"
                    : "right-1/2 bg-status-normal/70",
                )}
                style={{ width: `${halfWidthPct}%` }}
              />
            </div>
            <span
              className={cn(
                "intel-num text-right text-xs font-bold",
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
