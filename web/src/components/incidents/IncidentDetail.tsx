import { useEffect, useState, useCallback } from "react";
import {
  AlertOctagon, CheckCircle2, XCircle, Clock, Sparkles, ShieldQuestion,
  Lightbulb, ListChecks, BookOpen, Loader2,
} from "lucide-react";
import { apiFetch } from "../../lib/api";
import { cn } from "../../lib/utils";
import { relativeTime, titleCase } from "../../lib/format";
import { useAuth } from "../../store/AuthContext";
import type {
  IncidentDetail as IncidentDetailType, TimelineEntry, KnowledgeChunk,
} from "../../lib/types";

const SEVERITY_STYLE: Record<string, string> = {
  CRITICAL: "text-status-critical bg-status-critical/10 border-status-critical/40",
  WARNING: "text-status-warning bg-status-warning/10 border-status-warning/40",
  INFO: "text-status-offline bg-status-offline/10 border-status-offline/40",
};

const CONFIDENCE_STYLE: Record<string, string> = {
  HIGH: "text-status-normal",
  MEDIUM: "text-status-warning",
  LOW: "text-muted-foreground",
};

const LIKELIHOOD_STYLE: Record<string, string> = {
  HIGH: "bg-status-critical/15 text-status-critical",
  MEDIUM: "bg-status-warning/15 text-status-warning",
  LOW: "bg-secondary text-muted-foreground",
};

export function IncidentDetail({ incidentId }: { incidentId: string }) {
  const { user } = useAuth();
  const [incident, setIncident] = useState<IncidentDetailType | null>(null);
  const [timeline, setTimeline] = useState<TimelineEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionPending, setActionPending] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [closeNote, setCloseNote] = useState("");
  const [showCloseForm, setShowCloseForm] = useState(false);

  const load = useCallback(async () => {
    try {
      const [incRes, tlRes] = await Promise.all([
        apiFetch(`/incidents/${incidentId}`),
        apiFetch(`/incidents/${incidentId}/timeline`),
      ]);
      if (!incRes.ok) throw new Error("Failed to load incident");
      if (!tlRes.ok) throw new Error("Failed to load timeline");
      setIncident(await incRes.json());
      setTimeline(await tlRes.json());
      setError(null);
    } catch (e: any) {
      setError(e.message ?? "Failed to load incident");
    } finally {
      setLoading(false);
    }
  }, [incidentId]);

  useEffect(() => {
    load();
  }, [load]);

  const canManage = user?.role === "SUPERVISOR" || user?.role === "ADMIN";

  async function handleAck() {
    setActionPending(true);
    setActionError(null);
    try {
      const res = await apiFetch(`/incidents/${incidentId}/ack`, { method: "POST" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? `Failed (${res.status})`);
      }
      await load();
    } catch (e: any) {
      setActionError(e.message ?? "Acknowledge failed");
    } finally {
      setActionPending(false);
    }
  }

  async function handleClose() {
    if (!closeNote.trim()) return;
    setActionPending(true);
    setActionError(null);
    try {
      const res = await apiFetch(`/incidents/${incidentId}/close`, {
        method: "POST",
        body: JSON.stringify({ note: closeNote.trim() }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? `Failed (${res.status})`);
      }
      setShowCloseForm(false);
      setCloseNote("");
      await load();
    } catch (e: any) {
      setActionError(e.message ?? "Close failed");
    } finally {
      setActionPending(false);
    }
  }

  if (loading) {
    return (
      <div className="glass-card p-8 flex items-center justify-center text-muted-foreground gap-2">
        <Loader2 className="w-4 h-4 animate-spin" /> Loading incident&hellip;
      </div>
    );
  }
  if (error || !incident) {
    return (
      <div className="glass-card p-8 border-status-critical/50 bg-status-critical/10 text-status-critical flex items-center gap-3">
        <AlertOctagon /> {error ?? "Incident not found"}
      </div>
    );
  }

  const exp = incident.explanation;

  return (
    <div className="flex flex-col gap-6">
      <div className="glass-card p-6 flex flex-col gap-4">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <div className="flex items-center gap-3 flex-wrap">
              <span
                className={cn(
                  "px-2.5 py-1 rounded text-xs font-bold uppercase tracking-wide border",
                  SEVERITY_STYLE[incident.severity] ?? SEVERITY_STYLE.INFO,
                )}
              >
                {incident.severity}
              </span>
              <h1 className="text-2xl font-bold font-mono">{incident.machine_id}</h1>
              <span className="text-muted-foreground">{titleCase(incident.category)}</span>
              {incident.escalated && (
                <span className="text-[10px] uppercase font-bold px-1.5 py-0.5 rounded bg-status-critical/15 text-status-critical">
                  Escalated
                </span>
              )}
            </div>
            <div className="text-sm text-muted-foreground mt-2 flex items-center gap-2">
              <Clock className="w-3.5 h-3.5" />
              Opened {relativeTime(incident.opened_at)} &middot; {incident.event_count} events &middot; last
              activity {relativeTime(incident.last_event_at)}
            </div>
          </div>

          <div className="flex flex-col items-end gap-2">
            <StatusPill status={incident.status} />
            {canManage && incident.status !== "CLOSED" && (
              <div className="flex items-center gap-2">
                {incident.status === "OPEN" && (
                  <button
                    onClick={handleAck}
                    disabled={actionPending}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-semibold bg-secondary hover:bg-secondary/70 transition-colors disabled:opacity-50"
                  >
                    <CheckCircle2 className="w-3.5 h-3.5" /> Acknowledge
                  </button>
                )}
                <button
                  onClick={() => setShowCloseForm((v) => !v)}
                  disabled={actionPending}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-semibold bg-status-critical/15 text-status-critical hover:bg-status-critical/25 transition-colors disabled:opacity-50"
                >
                  <XCircle className="w-3.5 h-3.5" /> Close
                </button>
              </div>
            )}
          </div>
        </div>

        {showCloseForm && (
          <div className="flex flex-col gap-2 p-4 rounded-lg bg-secondary/40 border border-border intel-rise">
            <label className="text-xs uppercase tracking-wide text-muted-foreground font-medium">
              Close note (required)
            </label>
            <textarea
              value={closeNote}
              onChange={(e) => setCloseNote(e.target.value)}
              maxLength={500}
              rows={2}
              placeholder="What was done to resolve this?"
              className="bg-background/60 border border-border rounded-md p-2 text-sm resize-none focus:outline-none focus:ring-1 focus:ring-primary"
            />
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setShowCloseForm(false)}
                className="px-3 py-1.5 rounded text-xs font-medium text-muted-foreground hover:text-foreground transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleClose}
                disabled={actionPending || !closeNote.trim()}
                className="px-3 py-1.5 rounded text-xs font-semibold bg-primary text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-50"
              >
                Confirm close
              </button>
            </div>
          </div>
        )}

        {actionError && (
          <div className="text-sm text-status-critical flex items-center gap-2">
            <AlertOctagon className="w-4 h-4 shrink-0" /> {actionError}
          </div>
        )}
      </div>

      <ExplanationBlock exp={exp} timeline={timeline} />

      <div className="glass-card p-6 flex flex-col gap-4">
        <h2 className="text-lg font-semibold text-foreground">Timeline</h2>
        <div className="flex flex-col">
          {timeline.length === 0 ? (
            <p className="text-sm text-muted-foreground">No timeline entries recorded.</p>
          ) : (
            timeline.map((entry, i) => <TimelineRow key={entry.entry_key} entry={entry} isLast={i === timeline.length - 1} />)
          )}
        </div>
      </div>
    </div>
  );
}

function StatusPill({ status }: { status: string }) {
  const style =
    status === "OPEN"
      ? "bg-status-critical/15 text-status-critical"
      : status === "ACKNOWLEDGED"
        ? "bg-status-warning/15 text-status-warning"
        : "bg-status-normal/15 text-status-normal";
  return <span className={cn("px-3 py-1 rounded-full text-xs font-bold uppercase tracking-wide", style)}>{status}</span>;
}

function TimelineRow({ entry, isLast }: { entry: TimelineEntry; isLast: boolean }) {
  const dotColor =
    entry.kind === "EXPLANATION"
      ? "bg-primary"
      : entry.severity === "CRITICAL"
        ? "bg-status-critical"
        : entry.severity === "WARNING"
          ? "bg-status-warning"
          : "bg-status-offline";

  return (
    <div className="flex gap-4">
      <div className="flex flex-col items-center">
        <span className={cn("w-2.5 h-2.5 rounded-full mt-1.5 shrink-0", dotColor)} />
        {!isLast && <span className="w-px flex-1 bg-border/60 my-1" />}
      </div>
      <div className="pb-5 min-w-0">
        <div className="flex items-baseline gap-2 flex-wrap">
          <span className="text-sm font-medium text-foreground">{entry.summary}</span>
          {entry.count > 1 && (
            <span className="intel-num text-[11px] px-1.5 py-0.5 rounded bg-secondary text-muted-foreground">
              &times;{entry.count}
            </span>
          )}
        </div>
        <div className="text-xs text-muted-foreground mt-0.5">
          {relativeTime(entry.first_ts)}
          {entry.actor_id && ` · ${entry.actor_id}`}
        </div>
      </div>
    </div>
  );
}

function ExplanationBlock({ exp, timeline }: { exp: IncidentDetailType["explanation"]; timeline: TimelineEntry[] }) {
  const eventById = new Map(
    timeline
      .filter((t) => t.representative_event)
      .map((t) => [t.representative_event!.id, t]),
  );

  if (!exp) {
    return (
      <div className="glass-card p-6 flex items-center gap-3 text-muted-foreground">
        <Loader2 className="w-4 h-4 animate-spin" />
        <span className="text-sm">Explanation is being generated&hellip;</span>
      </div>
    );
  }

  const isFallback = exp.source === "FALLBACK";

  return (
    <div className="glass-card p-6 flex flex-col gap-5 intel-rise">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h2 className="text-lg font-semibold text-foreground">Explanation</h2>
        <div className="flex items-center gap-2">
          <span
            className={cn(
              "flex items-center gap-1.5 px-2.5 py-1 rounded text-xs font-semibold",
              isFallback ? "bg-secondary text-muted-foreground" : "bg-primary/15 text-primary",
            )}
            title={isFallback ? exp.fallback_reason ?? undefined : exp.model_name ?? undefined}
          >
            {isFallback ? <ShieldQuestion className="w-3.5 h-3.5" /> : <Sparkles className="w-3.5 h-3.5" />}
            {isFallback ? "Deterministic fallback" : `Groq (grounded)`}
          </span>
          <span className={cn("text-xs font-semibold uppercase", CONFIDENCE_STYLE[exp.confidence])}>
            {exp.confidence} confidence
          </span>
        </div>
      </div>

      {isFallback && exp.fallback_reason && (
        <p className="text-xs text-muted-foreground -mt-2">Generated without the LLM: {exp.fallback_reason}</p>
      )}

      <p className="text-sm text-foreground/90 leading-relaxed">{exp.summary}</p>

      {exp.probable_causes.length > 0 && (
        <Section icon={<ShieldQuestion className="w-4 h-4" />} title="Probable causes">
          <div className="flex flex-col gap-2">
            {exp.probable_causes.map((c, i) => (
              <div key={i} className="flex items-start gap-2.5 text-sm">
                <span
                  className={cn(
                    "shrink-0 px-1.5 py-0.5 rounded text-[10px] font-bold uppercase mt-0.5",
                    LIKELIHOOD_STYLE[c.likelihood],
                  )}
                >
                  {c.likelihood}
                </span>
                <div className="min-w-0">
                  <span className="text-foreground/90">{c.cause}</span>
                  <EvidenceChips refs={c.evidence_refs} eventById={eventById} />
                </div>
              </div>
            ))}
          </div>
        </Section>
      )}

      {exp.recommended_actions.length > 0 && (
        <Section icon={<ListChecks className="w-4 h-4" />} title="Recommended actions">
          <div className="flex flex-col gap-2.5">
            {exp.recommended_actions.map((a, i) => (
              <div key={i} className="text-sm">
                <div className="font-medium text-foreground/90">{a.action}</div>
                <div className="text-xs text-muted-foreground mt-0.5">{a.rationale}</div>
                <KnowledgeChips refs={a.knowledge_refs} />
              </div>
            ))}
          </div>
        </Section>
      )}

      {exp.lesson?.title && (
        <Section icon={<Lightbulb className="w-4 h-4" />} title="Lesson">
          <div className="text-sm">
            <div className="font-medium text-foreground/90">{exp.lesson.title}</div>
            <div className="text-xs text-muted-foreground mt-0.5">{exp.lesson.tip}</div>
            <KnowledgeChips refs={exp.lesson.knowledge_refs} />
          </div>
        </Section>
      )}
    </div>
  );
}

function Section({ icon, title, children }: { icon: React.ReactNode; title: string; children: React.ReactNode }) {
  return (
    <div className="pt-4 border-t border-border/50">
      <div className="flex items-center gap-2 text-xs uppercase tracking-wider text-muted-foreground font-medium mb-3">
        {icon}
        {title}
      </div>
      {children}
    </div>
  );
}

function EvidenceChips({ refs, eventById }: { refs: string[]; eventById: Map<string, TimelineEntry> }) {
  if (!refs.length) return null;
  return (
    <div className="flex flex-wrap gap-1.5 mt-1.5">
      {refs.map((ref) => {
        const entry = eventById.get(ref);
        return (
          <span
            key={ref}
            className="text-[11px] px-1.5 py-0.5 rounded-sm bg-secondary text-muted-foreground font-mono"
            title={entry ? entry.summary : ref}
          >
            {entry ? entry.event_type ?? "event" : ref.slice(0, 8)}
          </span>
        );
      })}
    </div>
  );
}

function KnowledgeChips({ refs }: { refs: string[] }) {
  const [chunks, setChunks] = useState<Record<string, KnowledgeChunk | "loading" | "error">>({});
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    refs.forEach((ref) => {
      if (chunks[ref]) return;
      setChunks((prev) => ({ ...prev, [ref]: "loading" }));
      apiFetch(`/knowledge/chunks/${encodeURIComponent(ref)}`)
        .then((res) => (res.ok ? res.json() : Promise.reject()))
        .then((data: KnowledgeChunk) => setChunks((prev) => ({ ...prev, [ref]: data })))
        .catch(() => setChunks((prev) => ({ ...prev, [ref]: "error" })));
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refs.join(",")]);

  if (!refs.length) return null;

  return (
    <div className="flex flex-col gap-1.5 mt-1.5">
      <div className="flex flex-wrap gap-1.5">
        {refs.map((ref) => {
          const chunk = chunks[ref];
          const label = chunk && chunk !== "loading" && chunk !== "error" ? chunk.heading || chunk.title : ref;
          return (
            <button
              key={ref}
              onClick={() => setOpen((prev) => (prev === ref ? null : ref))}
              disabled={chunk === "loading" || chunk === "error"}
              className={cn(
                "flex items-center gap-1 text-[11px] px-1.5 py-0.5 rounded-sm transition-colors",
                open === ref ? "bg-primary/20 text-primary" : "bg-secondary/70 text-muted-foreground hover:text-foreground",
              )}
            >
              <BookOpen className="w-3 h-3" />
              {chunk === "loading" ? "Loading…" : chunk === "error" ? "Unavailable" : label}
            </button>
          );
        })}
      </div>
      {open && chunks[open] && chunks[open] !== "loading" && chunks[open] !== "error" && (
        <div className="text-xs text-muted-foreground bg-background/40 rounded-md p-3 border border-border/50 intel-rise">
          {(chunks[open] as KnowledgeChunk).text}
        </div>
      )}
    </div>
  );
}
