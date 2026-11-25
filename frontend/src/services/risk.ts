import { request, requestCsv } from "./api"

export type Decision = "APPROVE" | "MANUAL_REVIEW" | "REJECT"
export type MLStatus = "ACTIVE" | "DIAGNOSTIC_ONLY" | "INSUFFICIENT_HISTORY" | "VALIDATION_FAILED" | "INFERENCE_FAILED"
export type EvidenceRef = { type: "payout" | "transaction" | "team"; id: string }
export type Totals = { count: number; cents: number }
export type DecisionSource = "AUTOMATED" | "HUMAN"
type HistoricalDecision = "AUTO_APPROVED" | "MANUAL_APPROVED" | "REJECTED"
export type DecisionCategory = "automatically_approved" | "analyst_approved" | "open_manual_review" | "rejected"
type Decimal = string | number | null
export type Metrics = Record<string, number | boolean | null | undefined>
type Gate = { code: string; passed: boolean; observed: number | null; limit: number }
export type ModelMetadata = {
  algorithm: string; feature_schema_version: string; status: MLStatus
  anomaly_threshold: number | null; metrics: Metrics; quality_gates: Gate[]
}
export type Model = ModelMetadata & {
  model_version_id: string
  created_at: string
  fit_cutoff_at: string | null
  calibration_cutoff_at: string | null
  validation_cutoff_at: string | null
  training_observation_count: number
  validation_observation_count: number
}
export type RunML = { status: MLStatus; payout_status_counts: Partial<Record<MLStatus, number>>; metadata: ModelMetadata }
export type Historical = {
  first_day: string; last_day: string; calendar_days: number; recorded_days: number
  non_settlement_days: number; unknown_days: number; denominator_days: number
  totals: Record<HistoricalDecision, Totals>
  daily_average_count: Decimal; daily_average_cents: Decimal
  current_vs_daily_average_count_percent: Decimal; current_vs_daily_average_cents_percent: Decimal
  days: {
    date_ist: string
    coverage: "recorded" | "no_payout_scheduled" | "unknown_source_coverage"
    totals: Record<HistoricalDecision, Totals> | null
  }[]
}
export type ClusterSummary = {
  cluster_id: string; stable_case_key: string; seed_payout_id: string; reason_code: string; summary: string
}
export type Dashboard = {
  evaluation: { run_id: string; batch_id: string; cutoff_at: string; snapshot_at: string | null; model_version_id: string | null }
  total: Totals; categories: Record<DecisionCategory, Totals>; historical: Historical | null; ml: RunML | null
  fraud_clusters: ClusterSummary[]
}
export type Payout = {
  payout_id: string; team_id: string; team_name: string; profession: string; team_status: string
  payout_amount_cents: number; captured_count: number; covers_from: string; covers_to: string; created_at: string
  original_decision: Decision; effective_decision: Decision; decision_source: DecisionSource
  effective_version: number; reason: string; cluster_id: string | null; ml_status: MLStatus; ml_is_anomaly: boolean | null
}
export type Queue = { items: Payout[]; total: number; limit: number; offset: number }
type DecisionVersion = {
  decision_source: DecisionSource; cluster_id: string | null; reason: string; evidence: EvidenceRef[]
  actor: { user_id: string; display_name: string } | null; history_id: string | null; timestamp: string
}
export type EffectiveDecision = DecisionVersion & {
  payout_id: string; run_id: string; decision_id: string
  original_decision: Decision; effective_decision: Decision; effective_version: number
}
export type DecisionEvent = DecisionVersion & {
  version: number; previous_decision: Decision | null; new_decision: Decision; status: "CURRENT" | "SUPERSEDED"
}
export type Audit = { effective: EffectiveDecision; history: DecisionEvent[] }
export type Override = {
  run_id: string; decision: Decision; reason: string; expected_version: number
  evidence: EvidenceRef[]; cluster_id: string | null
}
export type Attempt = {
  txn_id: string; created_at: string; amount_cents: number; tip_cents: number; collection_date_ist: string
  payment_method: string; status: "captured" | "declined" | "refunded"
}
export type Finding = {
  feature: string; unit: string; observed: Decimal; observation_availability: string; observed_denominator: number | null
  status: string; explanation: string; lower_fence: Decimal; upper_fence: Decimal; is_material: boolean | null
  minimum_absolute: Decimal; minimum_relative: Decimal; minimum_denominator: number; iqr_multiplier: Decimal
  current_transaction_ids: string[]
  baseline: {
    reliability: string; scope: "TEAM" | "PROFESSION"; sample_count: number; peer_team_count: number | null
    q1: Decimal; median: Decimal; q3: Decimal; iqr: Decimal; payout_ids: string[]; transaction_ids: string[]
  }
}
export type Comparison = { team: Finding; profession: Finding; preferred_scope: "TEAM" | "PROFESSION" | null }
type PathNode = { kind: string; source_id: string | null }
export type Path = {
  seed_payout_id: string
  steps: {
    start: PathNode; end: PathNode; relationship: string; strength: string; supports_exposure: boolean; evidence: EvidenceRef[]
  }[]
}
type Lead = { relationship: string; strength: string; seed_payout_id: string; current_evidence: EvidenceRef[]; historical_evidence: EvidenceRef[] }
export type ClusterMatch = Omit<Lead, "seed_payout_id"> & { stable_case_key: string; supporting_seed_ids: string[]; paths: Path[] }
export type Fraud = {
  status: string; established_exposure: boolean; explanation: string; current_evidence: EvidenceRef[]
  primary: ClusterMatch | null; alternatives: ClusterMatch[]; leads: Lead[]
}
export type Cluster = ClusterSummary & {
  reason_codes: string[]; evidence: EvidenceRef[]
  associated_payouts: { payout_id: string; original_cluster: boolean; effective_cluster: boolean; paths: Path[] }[]
}
export type ML = {
  status: MLStatus; anomaly_score: number | null; anomaly_threshold: number | null; is_anomaly: boolean | null
  observed_model_features: number; retained_model_features: number; qualifies_for_review: boolean
  diagnostic_code: string | null; explanation: string; evidence: EvidenceRef[]
  unusual_features: { feature: string; unit: string; observed: Decimal; historical_median: Decimal; comparison: string; evidence: EvidenceRef[] }[]
  feature_availability: { feature: string; availability: string }[]
}
export type Investigation = {
  payout: Payout; decisions: Audit; transactions: Attempt[]; statistics: Comparison[]; fraud: Fraud | null; ml: ML | null
}

const query = (runId: string) => new URLSearchParams({ run_id: runId })
export const getDashboard = async (runId: string, signal: AbortSignal) =>
  (await request<Dashboard>(`/api/dashboard?${query(runId)}`, { signal })).data
export const getQueue = async (runId: string, filters: Record<string, string>, signal: AbortSignal) =>
  (await request<Queue>(`/api/payouts?${new URLSearchParams({ ...filters, run_id: runId })}`, { signal })).data
export const getInvestigation = async (runId: string, id: string, signal: AbortSignal) =>
  (await request<Investigation>(`/api/payouts/${encodeURIComponent(id)}?${query(runId)}`, { signal })).data
export const getHistory = async (runId: string, id: string, signal: AbortSignal) =>
  (await request<Audit>(`/api/payouts/${encodeURIComponent(id)}/history?${query(runId)}`, { signal })).data
export const getClusters = async (runId: string, signal: AbortSignal) =>
  (await request<Cluster[]>(`/api/fraud-clusters?${query(runId)}`, { signal })).data
export const getModels = async (offset: number, signal: AbortSignal) =>
  (await request<Model[]>(`/api/models?limit=20&offset=${offset}`, { signal })).data
export const overridePayout = async (id: string, body: Override) =>
  (await request<EffectiveDecision>(`/api/payouts/${encodeURIComponent(id)}/overrides`, { write: true, body })).data
export const exportDecisions = (runId: string) => requestCsv(`/api/exports/decisions.csv?${query(runId)}`)
