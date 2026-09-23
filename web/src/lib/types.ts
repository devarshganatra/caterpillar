// TS mirrors of contracts/intelligence.py + contracts/events.py (Stage 3 Batch 3I).
// Kept intentionally permissive (optional fields) since the backend sends
// explicit nulls/UNAVAILABLE rather than omitting keys, but this file is
// the UI's contract, not a runtime validator.

export type EtaStatus = "OK" | "UNAVAILABLE";

export interface EtaFactor {
  group: string;
  minutes: number;
  top_feature: string;
  top_feature_value: string | number | null;
}

export interface EtaEstimate {
  task_id: string;
  machine_id: string;
  window_id: string | null;
  ts: string;
  status: EtaStatus;
  unavailable_reason?: string | null;
  model_version?: string | null;
  baseline_p10_min?: number | null;
  baseline_p50_min?: number | null;
  baseline_p90_min?: number | null;
  remaining_p10_min?: number | null;
  remaining_p50_min?: number | null;
  remaining_p90_min?: number | null;
  eta_total_p50_min?: number | null;
  progress?: number | null;
  blend_weight_model?: number | null;
  cycles_done?: number | null;
  target_cycles?: number | null;
  planner_estimate_min?: number | null;
  factors: EtaFactor[];
  base_value_min?: number | null;
  defaults_used: string[];
  slip_pct?: number | null;
  time_unit: "sim_minutes";
}

export type IdleCause = "PLANNED" | "MACHINE" | "WEATHER" | "SITE" | "OPERATOR";

export interface IdleAttribution {
  window_id: string;
  machine_id: string;
  operator_id: string;
  window_start: string;
  window_end: string;
  idle_seconds: number;
  breakdown_s: Partial<Record<IdleCause, number>>;
  primary_cause: IdleCause | null;
  evidence: Record<string, unknown>;
  operator_idle_ratio: number;
  deviation_status: "OK" | "UNAVAILABLE";
  expected_idle_ratio?: number | null;
  deviation_ratio?: number | null;
  robust_z?: number | null;
  baseline_level?: string | null;
  operator_deviation_flag: boolean;
  consecutive_windows: number;
}

export interface AnomalyDriver {
  feature: string;
  value: number;
  robust_z: number;
  shap: number | null;
}

export type AnomalyMethod = "IFOREST" | "ROBUST_Z" | "SKIPPED" | "UNAVAILABLE";

export interface AnomalyResult {
  window_id: string;
  machine_id: string;
  method: AnomalyMethod;
  reason?: string | null;
  score?: number | null;
  threshold?: number | null;
  is_anomalous: boolean;
  drivers: AnomalyDriver[];
  drivers_method?: string | null;
  model_version?: string | null;
}

export type IncidentStatus = "OPEN" | "ACKNOWLEDGED" | "CLOSED";
export type IncidentSeverity = "INFO" | "WARNING" | "CRITICAL";
export type ExplanationStatus = "PENDING" | "READY" | "FALLBACK" | "FAILED";

export interface IncidentSummary {
  id: string;
  machine_id: string;
  site_id: string;
  operator_id: string | null;
  task_id: string | null;
  category: string;
  severity: IncidentSeverity;
  escalated: boolean;
  status: IncidentStatus;
  opened_at: string;
  last_event_at: string;
  event_count: number;
  explanation_status: ExplanationStatus;
}

export interface ProbableCause {
  cause: string;
  evidence_refs: string[];
  likelihood: "LOW" | "MEDIUM" | "HIGH";
}

export interface RecommendedAction {
  action: string;
  rationale: string;
  knowledge_refs: string[];
}

export interface Lesson {
  title: string;
  tip: string;
  knowledge_refs: string[];
}

export interface IncidentExplanation {
  source: "GROQ" | "FALLBACK";
  model_name: string | null;
  summary: string;
  probable_causes: ProbableCause[];
  recommended_actions: RecommendedAction[];
  lesson: Lesson;
  training_refs: string[];
  confidence: "LOW" | "MEDIUM" | "HIGH";
  fallback_reason: string | null;
  created_at: string;
}

export interface IncidentDetail extends IncidentSummary {
  explanation: IncidentExplanation | null;
}

export interface RepresentativeEvent {
  id: string;
  type: string;
  severity: string;
  ts: string;
  evidence: Record<string, unknown>;
}

export interface TimelineEntry {
  entry_key: string;
  kind: "EVENT" | "STATUS" | "EXPLANATION";
  event_type: string | null;
  severity: string | null;
  first_ts: string;
  last_ts: string;
  count: number;
  summary: string;
  actor_id: string | null;
  representative_event: RepresentativeEvent | null;
}

export interface MachineHotState {
  state: string | null;
  ui_mode: "HUD" | "IDLE_HUB" | null;
  risk_score: number | null;
  risk_level: string | null;
  last_seq: number | null;
  ts: string | null;
  stale: boolean;
}

export interface EnvelopeState {
  red_radius_m: number;
  orange_radius_m: number;
  speed_cap_kmh: number;
  condition_multiplier: number;
  active_conditions: string[];
  notes?: string;
}

export interface LatestWindow {
  window_id: string;
  idle_attribution: IdleAttribution | null;
  anomaly: AnomalyResult | null;
}

export interface MachineSnapshot {
  machine_id: string;
  site_id: string;
  snapshot_ts: string;
  hot: MachineHotState | null;
  envelope: EnvelopeState | null;
  context: Record<string, unknown> | null;
  recent_events: RepresentativeEvent[];
  latest_window: LatestWindow | null;
  eta: EtaEstimate | { status: "UNAVAILABLE"; unavailable_reason: string };
  open_incidents: IncidentSummary[];
}

export interface WindowsResponse {
  items: Array<{
    window_id: string;
    window_start: string;
    window_end: string;
    frame_count: number;
    idle_attribution: IdleAttribution | null;
    anomaly: AnomalyResult | null;
    eta_slip_band: number;
  }>;
  totals_s: Partial<Record<IdleCause, number>>;
}

export interface KnowledgeChunk {
  chunk_id: string;
  doc_id: string;
  title: string;
  heading: string;
  text: string;
}

export interface WorkerStatus {
  workers: Record<string, Record<string, string>>;
  stream_lag: Record<string, { pending: number; lag: number; entries_read: number }>;
}
