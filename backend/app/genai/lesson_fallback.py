"""Deterministic fallback lesson (Stage 4A) — same guarantee as
genai/fallback.py's incident explanation fallback: Groq being unavailable
must never leave an incident without SOME lesson for its operator. No
randomness: the same packet always produces byte-identical output."""
from __future__ import annotations

from backend.app.genai.lesson_schemas import LessonLLM

_TIP_TEXT = {
    "SEATBELT_VIOLATION": "Fasten your seatbelt before the machine starts moving or working — every time, no exceptions.",
    "PROXIMITY_BREACH": "Treat every proximity alert as real: slow down and confirm the area is clear before continuing.",
    "OVERSPEED_CONDITION": "Match your speed to current conditions, not just the posted limit — rain and mud lower the safe speed.",
    "HEALTH_THRESHOLD": "If a gauge crosses its safe threshold, stop and report it — don't wait to see if it corrects itself.",
    "OPERATIONAL_ANOMALY": "An unusual reading is worth a second look, even if nothing feels wrong from the cab.",
    "IDLE_DEVIATION": "If you're idling more than usual, check whether it's a truck/queue issue before assuming it's on you.",
    "ETA_SLIP": "A slow start to a task is often recoverable — flag it early rather than trying to make up time by rushing.",
}

_DEFAULT_TIP = "Review what happened in this incident and keep it in mind for similar conditions."

_EXPLANATION_TEXT = {
    "SEATBELT_VIOLATION": "This incident was triggered by the seatbelt being unfastened while the machine was moving or working. Seatbelts are the primary protection against injury if the machine tips or stops suddenly — the few seconds it takes to fasten one is far cheaper than the alternative.",
    "PROXIMITY_BREACH": "This incident was triggered by a person or obstacle detected close to the machine. Proximity systems exist because blind spots on heavy equipment are large, and a moment's distraction is enough for someone to end up too close.",
    "OVERSPEED_CONDITION": "This incident was triggered by the machine exceeding its speed cap for current conditions. Speed caps adjust for weather and ground conditions specifically because stopping distance changes a lot more than most operators expect.",
    "HEALTH_THRESHOLD": "This incident was triggered by a machine health reading crossing its safe operating threshold. Small warning signs like this are usually the cheapest point to catch a developing mechanical issue, before it becomes a breakdown or a safety event.",
    "OPERATIONAL_ANOMALY": "This incident was triggered by the machine's operating pattern looking statistically unusual compared to its normal behavior. This doesn't always mean something is wrong, but it's a signal worth a quick check rather than ignoring.",
    "IDLE_DEVIATION": "This incident was triggered by idle time running higher than expected for the current task and conditions. Idle time has several possible causes — site, weather, or operator — so it's worth understanding which one applies before changing anything.",
    "ETA_SLIP": "This incident was triggered by the task's estimated completion time slipping noticeably behind the baseline. Catching a slip early gives more options than noticing it only once the task is badly behind.",
}

_DEFAULT_EXPLANATION = "This incident was recorded by the safety system based on the conditions and events at the time. Understanding what happened helps recognize similar situations before they escalate."


def _best_knowledge_chunk_for_tag(knowledge: list[dict], tag: str) -> dict | None:
    for chunk in knowledge:
        if tag in (chunk.get("tags") or []):
            return chunk
    return knowledge[0] if knowledge else None


def build_lesson_fallback(packet: dict) -> LessonLLM:
    incident = packet["incident"]
    category = incident["category"]
    knowledge = packet.get("knowledge", [])

    if not knowledge:
        # Same guarantee as build_fallback() in genai/fallback.py: this can
        # only happen if the knowledge base itself failed to load (a
        # deployment problem), and there's no real chunk_id to cite —
        # surface it loudly rather than build a schema-invalid lesson.
        raise RuntimeError(
            "build_lesson_fallback: packet has no knowledge chunks — knowledge base failed to load; "
            "cannot construct a schema-valid lesson without a real knowledge_refs id"
        )

    chunk = _best_knowledge_chunk_for_tag(knowledge, category)
    tip = _TIP_TEXT.get(category, _DEFAULT_TIP)
    explanation = _EXPLANATION_TEXT.get(category, _DEFAULT_EXPLANATION)
    title = f"{category.replace('_', ' ').title()} — What To Watch For"

    return LessonLLM(
        title=title[:80],
        short_tip=tip[:200],
        explanation=explanation[:800],
        knowledge_refs=[chunk["chunk_id"]],
    )
