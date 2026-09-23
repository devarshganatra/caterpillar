// Small, shared formatting helpers for the intelligence-layer UI — kept in
// one place so "how we write a duration" never drifts between components.

export function fmtMinutes(min: number | null | undefined): string {
  if (min == null || Number.isNaN(min)) return "—";
  const clamped = Math.max(0, min);
  if (clamped < 1) return "<1m";
  const h = Math.floor(clamped / 60);
  const m = Math.round(clamped % 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

export function fmtSignedMinutes(min: number): string {
  const sign = min > 0.05 ? "+" : min < -0.05 ? "−" : "";
  return `${sign}${fmtMinutes(Math.abs(min))}`;
}

export function fmtPercent(ratio: number | null | undefined, digits = 0): string {
  if (ratio == null || Number.isNaN(ratio)) return "—";
  return `${(ratio * 100).toFixed(digits)}%`;
}

export function fmtSeconds(sec: number | null | undefined): string {
  if (sec == null || Number.isNaN(sec)) return "—";
  return fmtMinutes(sec / 60);
}

export function titleCase(s: string): string {
  return s
    .toLowerCase()
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

export function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  const diffSec = Math.round((Date.now() - then) / 1000);
  if (diffSec < 5) return "just now";
  if (diffSec < 60) return `${diffSec}s ago`;
  const diffMin = Math.round(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.round(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.round(diffHr / 24);
  return `${diffDay}d ago`;
}
