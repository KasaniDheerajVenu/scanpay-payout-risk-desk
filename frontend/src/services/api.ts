type ErrorDetails = { code?: string; file_type?: string; row?: number; field?: string }

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly details: ErrorDetails,
    readonly retryAfter: string | null = null,
  ) {
    super(`Request failed (${status})`)
  }
}

const expiryListeners = new Set<() => void>()
let sessionEpoch = 0

export function advanceSession() { sessionEpoch += 1 }

export function onSessionExpiry(listener: () => void) {
  expiryListeners.add(listener)
  return () => { expiryListeners.delete(listener) }
}

type Options = { signal?: AbortSignal; body?: FormData | object; write?: boolean; anonymous?: boolean }

export async function request<T>(path: `/api/${string}`, options: Options = {}): Promise<{
  data: T
  status: number
}> {
  const response = await send(path, options, "application/json")
  return { data: (response.status === 204 ? undefined : await response.json()) as T, status: response.status }
}

export async function requestCsv(path: `/api/${string}`) {
  const response = await send(path, {}, "text/csv")
  if (response.headers.get("Content-Type")?.split(";")[0].trim().toLowerCase() !== "text/csv") {
    throw new ApiError(502, {})
  }
  return response.blob()
}

async function send(path: `/api/${string}`, options: Options, accept: string) {
  const headers = new Headers({ Accept: accept })
  const epoch = sessionEpoch
  const { signal, body, write, anonymous } = options
  if (write) {
    const { data } = await request<{ csrf_token: string }>("/api/auth/csrf", { signal, anonymous })
    headers.set("X-CSRF-Token", data.csrf_token)
  }
  const form = body instanceof FormData
  if (body && !form) headers.set("Content-Type", "application/json")
  const response = await fetch(path, {
    headers, signal, credentials: "same-origin", method: write ? "POST" : "GET",
    body: form ? body : body ? JSON.stringify(body) : undefined,
  })
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as {
      error?: { code?: string; details?: ErrorDetails }
    } | null
    if (response.status === 401 && !anonymous && epoch === sessionEpoch) {
      expiryListeners.forEach((listener) => listener())
    }
    throw new ApiError(response.status, {
      ...payload?.error?.details, code: payload?.error?.details?.code ?? payload?.error?.code,
    }, response.headers.get("Retry-After"))
  }
  return response
}

const issueLabels: Record<string, string> = {
  bad_header: "The CSV columns do not match the required schema.",
  invalid_header: "The CSV columns do not match the required schema.",
  missing_file: "A required CSV file is missing.",
  empty_file: "The CSV file is empty.",
  invalid_value: "A field contains an invalid value.",
  assessment_count_mismatch: "The initial dataset does not match the assessment counts.",
  unknown_team: "The referenced team is missing.",
  initial_import_required: "Import an initial dataset before importing incremental files.",
  evaluation_in_progress: "An equivalent evaluation is running. Refresh the run history before retrying.",
  dataset_batch_not_ready: "Choose a dataset with Ready status.",
  processing_failed: "The evaluation could not be processed.",
  publication_failed: "The evaluation results could not be published.",
  attempt_expired: "The previous execution expired. Run the evaluation again.",
  unchanged_decision: "Choose a different decision.",
  invalid_override_evidence: "Choose supporting records from this payout’s published evidence.",
  review_evidence_required: "Manual review requires a related historical payout or transaction.",
  invalid_override_cluster: "Choose a fraud cluster from this evaluation.",
  override_exposure_not_established: "Reject requires the complete evidence for a published exposure path.",
}

export function issueMessage(code?: string | null) {
  return code ? issueLabels[code] : undefined
}

export function errorMessage(error: unknown, context?: "login" | "import" | "evaluation") {
  if (!(error instanceof ApiError)) return "The server could not be reached. Check your connection and retry."
  const fallback: Record<number, string> = {
    401: context === "login" ? "The username or password is incorrect." : "Your session expired. Sign in again.",
    403: context === "login"
      ? "Sign-in verification expired. Try signing in again."
      : "This action is restricted or verification expired. Refresh and try again.",
    404: "This record is unavailable. Select another record.",
    409: context === "import"
      ? "The dataset conflicts with previously imported records. Check the files and retry."
      : "The operation conflicts with an existing request. Refresh and try again.",
    413: "The files exceed the upload limits. Reduce their size and try again.",
    422: context === "import"
      ? "The files did not pass validation. Check the CSV format and the selected cutoff."
      : "Check the submitted values and try again.",
    429: error.retryAfter && /^\d+$/.test(error.retryAfter)
      ? `Too many sign-in attempts. Try again in ${error.retryAfter} seconds.`
      : "Too many requests. Wait a moment before trying again.",
    503: "The service is temporarily unavailable. Try again shortly.",
  }
  return issueMessage(error.details.code) ?? fallback[error.status] ?? "The request failed. Try again."
}
