"""
Deterministic fallback explanation (Stage 3 Batch 3H) — used whenever the
LLM is unavailable, times out, or its output fails grounding validation
twice. No randomness: the same packet always produces byte-identical
output, and the incident always gets an explanation either way — the
system must never leave an incident unexplained just because Groq is down.
"""
from __future__ import annotations

from backend.app.genai.schemas import IncidentExplanationLLM, ProbableCause, RecommendedAction, Lesson

_CAUSE_TEXT = {
    "SEATBELT_VIOLATION": "Seatbelt was unfastened while the machine was {state}",
    "PROXIMITY_BREACH": "A person or obstacle was detected within the {zone} proximity zone",
    "OVERSPEED_CONDITION": "Machine speed exceeded the condition-adjusted cap",
    "HEALTH_THRESHOLD": "{metric} exceeded its safe operating threshold",
    "OPERATIONAL_ANOMALY": "The operating pattern for this window was statistically unusual",
    "IDLE_DEVIATION": "Idle time was above the expected baseline for this context",
    "ETA_SLIP": "Task duration is running behind the baseline estimate",
}

_ACTION_TEXT = {
    "SEATBELT_VIOLATION": ("Stop non-essential motion, refasten the seatbelt, and confirm before resuming work.",
                            "Prevents injury from ejection during a rollover or sudden stop."),
    "PROXIMITY_BREACH": ("Slow down, sound the horn if unsure, and confirm the area is clear before continuing.",
                          "Reduces the risk of striking a person or obstacle."),
    "OVERSPEED_CONDITION": ("Reduce speed to within the active condition-adjusted cap.",
                             "Keeps stopping distance and control within the safety envelope for current conditions."),
    "HEALTH_THRESHOLD": ("Stop the current operation and report the machine for a maintenance check.",
                          "Continuing to operate past a health threshold risks component damage or failure."),
    "OPERATIONAL_ANOMALY": ("Review recent maintenance history and compare against task/conditions before assuming an operator issue.",
                             "Fuel and cycle-time anomalies have multiple possible causes that need to be narrowed down."),
    "IDLE_DEVIATION": ("Check the idle attribution breakdown before addressing this directly with the operator.",
                        "Idle time often has a site or weather cause rather than an operator cause."),
    "ETA_SLIP": ("Check task progress against elapsed time before assuming the whole task will run proportionally longer.",
                 "A slow start is often recoverable; a sustained slowdown is a different situation."),
}

DEFAULT_CAUSE_TEXT = "An event of type {event_type} was recorded during this incident"
DEFAULT_ACTION_TEXT = ("Review the incident timeline and evidence before taking further action.",
                        "No specific guidance is available for this event type.")


def _format_evidence(event_type: str, evidence: dict) -> str:
    template = _CAUSE_TEXT.get(event_type, DEFAULT_CAUSE_TEXT.format(event_type=event_type))
    try:
        return template.format(**evidence)
    except (KeyError, IndexError):
        return template.format(**{k: evidence.get(k, "unknown") for k in ("state", "zone", "metric")}) \
            if "{" in template else template


def _best_knowledge_chunk_for_tag(knowledge: list[dict], tag: str) -> str | None:
    for chunk in knowledge:
        if tag in (chunk.get("tags") or []):
            return chunk["chunk_id"]
    return knowledge[0]["chunk_id"] if knowledge else None


def build_fallback(packet: dict, reason: str) -> IncidentExplanationLLM:
    incident = packet["incident"]
    timeline = packet.get("timeline", [])
    events_by_id = {e["event_id"]: e for e in packet.get("events", [])}
    knowledge = packet.get("knowledge", [])

    event_entries = [t for t in timeline if t["kind"] == "EVENT"]
    distinct_types_in_order: list[str] = []
    for t in event_entries:
        if t["event_type"] and t["event_type"] not in distinct_types_in_order:
            distinct_types_in_order.append(t["event_type"])

    t0 = event_entries[0]["first_ts"] if event_entries else incident["opened_at"]
    t1 = event_entries[-1]["last_ts"] if event_entries else incident["last_event_at"]
    top_entries = ", ".join(
        f"{t['event_type']} x{t['count']}" for t in sorted(event_entries, key=lambda t: -t["count"])[:3]
    )
    summary = (
        f"{incident['severity']} incident on {incident['machine_id']}: "
        f"{len(distinct_types_in_order)} event types between {t0} and {t1} ({top_entries}). "
        f"Explanation generated without the LLM ({reason})."
    )

    probable_causes: list[ProbableCause] = []
    for event_type in distinct_types_in_order[:4]:
        matching_entry = next(t for t in event_entries if t["event_type"] == event_type)
        rep_id = matching_entry.get("representative_event_id")
        evidence = events_by_id.get(rep_id, {}).get("evidence", {}) if rep_id else {}
        cause_text = _format_evidence(event_type, evidence)
        probable_causes.append(ProbableCause(
            # rep_id should always be set for an EVENT-kind timeline entry
            # (correlator.py always assigns one), but fall back to the
            # incident's own trigger_event_id rather than risk an empty
            # list, which ProbableCause's min_length=1 would reject.
            cause=cause_text, evidence_refs=[rep_id or incident["trigger_event_id"]], likelihood="MEDIUM",
        ))
    if not probable_causes:
        # Every incident is guaranteed to have a trigger_event_id (FK
        # constraint on incidents.trigger_event_id) — use it as the
        # evidence_refs entry here, since ProbableCause.evidence_refs
        # requires min_length=1 and an empty list would fail to construct.
        probable_causes.append(ProbableCause(
            cause=f"{incident['category']} triggered this incident",
            evidence_refs=[incident["trigger_event_id"]], likelihood="LOW",
        ))

    recommended_actions: list[RecommendedAction] = []
    for event_type in distinct_types_in_order[:5] or [incident["category"]]:
        action_text, rationale = _ACTION_TEXT.get(event_type, DEFAULT_ACTION_TEXT)
        chunk_id = _best_knowledge_chunk_for_tag(knowledge, event_type)
        recommended_actions.append(RecommendedAction(
            action=action_text, rationale=rationale, knowledge_refs=[chunk_id] if chunk_id else [],
        ))

    if not knowledge:
        # Lesson.knowledge_refs requires min_length=1 (so does
        # probable_causes[].evidence_refs indirectly via validate_grounding
        # needing SOME allowed_chunk_ids to exist), and there is no
        # non-fabricated chunk_id to put there. This only happens if the
        # knowledge base failed to load entirely (a deployment/config
        # problem, not an LLM problem) — surface it loudly instead of
        # silently building an explanation that can never pass grounding.
        raise RuntimeError(
            "build_fallback: packet has no knowledge chunks — knowledge base failed to load; "
            "cannot construct a schema-valid Lesson without a real knowledge_refs id"
        )

    top_chunk = knowledge[0]
    first_sentence = top_chunk["text"].split(". ")[0].strip()
    if not first_sentence.endswith("."):
        first_sentence += "."
    lesson = Lesson(
        title=top_chunk["heading"][:80], tip=first_sentence[:400], knowledge_refs=[top_chunk["chunk_id"]],
    )

    return IncidentExplanationLLM(
        summary=summary[:600],
        probable_causes=probable_causes,
        recommended_actions=recommended_actions,
        lesson=lesson,
        training_refs=[k["chunk_id"] for k in knowledge[:3]],
        confidence="LOW",
    )
