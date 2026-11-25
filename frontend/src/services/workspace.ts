import { ApiError, request } from "./api"
import type { Model, RunML } from "./risk"

export type Permission = "read_risk" | "view_audit" | "upload_datasets" | "run_evaluations"
  | "import_synthetic_demo" | "override_decisions" | "export_decisions"

export type Session = {
  user_id: string
  username: string
  display_name: string
  role: "ADMIN" | "ANALYST" | "VIEWER"
  mode: "local" | "demo"
  permissions: Permission[]
}

export type FileType = "teams" | "history_transactions" | "history_payouts" | "latest_transactions" | "pending_payouts"
export type ImportMode = "INITIAL" | "INCREMENTAL"
export type ValidationIssue = { code: string; file_type: FileType | null; row: number | null; field: string | null }
export type Batch = {
  batch_id: string
  status: "UPLOADED" | "VALIDATING" | "READY" | "FAILED"
  mode: ImportMode
  cutoff: string
  imported_at: string | null
  counts: { rows: number; inserted: number; unchanged: number; dispositions_completed: number }
  validation: ValidationIssue | null
}
export type BatchDetail = Batch & {
  files: {
    file_type: FileType; filename: string; sha256: string; bytes_count: number
    row_count: number; inserted_count: number; unchanged_count: number
    validation_summary: { dispositions_completed: number; incomplete_historical_windows: number }
  }[]
}
export type Evaluation = {
  run_id: string
  batch_id: string
  cutoff_at: string
  created_at: string
  started_at: string | null
  completed_at: string | null
  status: "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED"
  rule_version: string
  failure: string | null
  summary: {
    payout_count: number
    total_cents: number
    decisions: Record<"APPROVE" | "MANUAL_REVIEW" | "REJECT", { count: number; cents: number }>
    ml: Pick<RunML, "status"> & Partial<Omit<RunML, "status">>
  } | null
  model: Model | null
}

export const sessionKey = ["auth", "me"] as const
export const batchKey = ["datasets"] as const
export const runKey = ["evaluations"] as const

export async function getSession(signal: AbortSignal) {
  try {
    return (await request<Session>("/api/auth/me", { signal, anonymous: true })).data
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) return null
    throw error
  }
}

export const signIn = async (username: string, password: string) =>
  (await request<Session>("/api/auth/login", { write: true, anonymous: true, body: { username, password } })).data
export const signOut = () => request<void>("/api/auth/logout", { write: true })
export const getBatches = async (signal: AbortSignal) =>
  (await request<Batch[]>("/api/datasets", { signal })).data
export const getBatch = async (id: string, signal: AbortSignal) =>
  (await request<BatchDetail>(`/api/datasets/${encodeURIComponent(id)}`, { signal })).data
export const uploadBatch = (body: FormData) =>
  request<BatchDetail>("/api/datasets/upload", { write: true, body })
export const demoImport = (preset_id: string) =>
  request<BatchDetail>("/api/datasets/demo-import", { write: true, body: { preset_id } })
export const getRuns = async (signal: AbortSignal, offset = 0, batchId?: string) => {
  const query = new URLSearchParams({ offset: String(offset), limit: "20" })
  if (batchId) query.set("batch_id", batchId)
  return (await request<Evaluation[]>(`/api/evaluations?${query}`, { signal })).data
}
export const getRun = async (id: string, signal: AbortSignal) =>
  (await request<Evaluation>(`/api/evaluations/${encodeURIComponent(id)}`, { signal })).data
export const startRun = (batch_id: string) =>
  request<Evaluation>("/api/evaluations", { write: true, body: { batch_id } })
