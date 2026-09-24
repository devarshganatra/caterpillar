import { useEffect, useState } from "react";
import { GraduationCap, BookOpen, Sparkles, ShieldQuestion, Loader2 } from "lucide-react";
import { apiFetch } from "../../lib/api";
import { cn } from "../../lib/utils";
import type { LessonDetail, LessonPush } from "../../lib/types";

type LessonContent = LessonDetail | LessonPush;

/**
 * Stage 4C lesson player. Prefers the live `lesson_ready` WS push (full
 * content, no extra round trip); falls back to GET /lessons?limit=1 so a
 * page reload during IDLE_HUB doesn't lose a lesson that already arrived
 * server-side (delivered_at is the real source of truth, not this push).
 */
export function LessonPlayer({ livePush }: { livePush: LessonPush | null }) {
  const [lesson, setLesson] = useState<LessonContent | null>(livePush);
  const [loading, setLoading] = useState(!livePush);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (livePush) {
      setLesson(livePush);
      setLoading(false);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const listRes = await apiFetch("/lessons?limit=1");
        if (!listRes.ok) throw new Error("Failed to load lessons");
        const list = await listRes.json();
        const latest = list.items?.[0];
        if (!latest) {
          if (!cancelled) setLesson(null);
          return;
        }
        const detailRes = await apiFetch(`/lessons/${latest.id}`);
        if (detailRes.ok && !cancelled) setLesson(await detailRes.json());
      } catch (e: any) {
        if (!cancelled) setError(e.message ?? "Failed to load lesson");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [livePush]);

  return (
    <div className="glass-card p-6">
      <div className="flex items-center gap-3 mb-5">
        <GraduationCap className="w-5 h-5 text-primary" />
        <h2 className="text-base font-bold text-foreground">Micro-Lesson</h2>
        {lesson && (
          <span
            className={cn(
              "ml-auto flex items-center gap-1.5 px-2 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide",
              lesson.source === "FALLBACK" ? "bg-secondary text-muted-foreground" : "bg-primary/15 text-primary",
            )}
          >
            {lesson.source === "FALLBACK" ? <ShieldQuestion className="w-3 h-3" /> : <Sparkles className="w-3 h-3" />}
            {lesson.source === "FALLBACK" ? "Deterministic" : "Grounded"}
          </span>
        )}
      </div>

      {loading ? (
        <div className="flex items-center justify-center min-h-[100px] gap-2 text-sm text-muted-foreground">
          <Loader2 className="w-4 h-4 animate-spin" /> Loading&hellip;
        </div>
      ) : error ? (
        <p className="text-sm text-status-critical">{error}</p>
      ) : !lesson ? (
        <div className="flex flex-col items-center justify-center min-h-[100px] border-2 border-dashed border-border/50 rounded-lg bg-secondary/20 gap-2">
          <BookOpen className="w-8 h-8 text-muted-foreground/30" />
          <p className="text-sm text-muted-foreground">No pending lessons.</p>
        </div>
      ) : (
        <div className="flex flex-col gap-4 intel-rise">
          <div>
            <h3 className="text-lg font-bold text-foreground">{lesson.title}</h3>
            <p className="text-sm text-primary font-medium mt-1">{lesson.short_tip}</p>
          </div>
          <p className="text-sm text-foreground/80 leading-relaxed">{lesson.explanation}</p>

          {lesson.knowledge_refs.length > 0 && (
            <div className="flex flex-wrap gap-1.5 pt-1">
              {lesson.knowledge_refs.map((ref) => (
                <span
                  key={ref}
                  className="flex items-center gap-1 text-[11px] px-1.5 py-0.5 rounded-sm bg-secondary/70 text-muted-foreground"
                  title={ref}
                >
                  <BookOpen className="w-3 h-3" />
                  {ref}
                </span>
              ))}
            </div>
          )}

          <div className="pt-3 border-t border-border/40 flex items-center justify-between">
            <span className="text-[11px] text-muted-foreground/60">From this incident's explanation</span>
            <button
              disabled
              title="Replay quiz not yet implemented"
              className="text-xs font-semibold px-3 py-1.5 rounded bg-secondary text-muted-foreground/50 cursor-not-allowed"
            >
              Start Quiz &middot; Coming soon
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
