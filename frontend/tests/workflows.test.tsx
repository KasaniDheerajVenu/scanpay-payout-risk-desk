import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { MemoryRouter } from "react-router"
import { afterEach, describe, expect, it, vi } from "vitest"
import { App } from "@/App"
import { fileProblem, fileTypes, makeUpload, requiredFiles } from "@/features/datasets/files"
import type { BatchDetail, Evaluation, Session } from "@/services/workspace"

const admin: Session = {
  user_id: "synthetic-admin", username: "operator", display_name: "Test Operator",
  role: "ADMIN", mode: "local", permissions: ["read_risk", "view_audit", "upload_datasets", "run_evaluations"],
}
const batch: BatchDetail = {
  batch_id: "batch-ready", status: "READY", mode: "INITIAL", cutoff: "2025-02-20T04:30:00Z",
  imported_at: "2025-02-20T05:00:00Z",
  counts: { rows: 23, inserted: 20, unchanged: 3, dispositions_completed: 0 },
  validation: null, files: [],
}
const run: Evaluation = {
  run_id: "run-new", batch_id: batch.batch_id, status: "COMPLETED", rule_version: "hybrid-v1",
  cutoff_at: batch.cutoff, created_at: batch.cutoff, started_at: batch.cutoff,
  completed_at: batch.cutoff, failure: null, model: null,
  summary: { payout_count: 2, total_cents: 3625, ml: { status: "DIAGNOSTIC_ONLY" }, decisions: {
    APPROVE: { count: 0, cents: 0 }, MANUAL_REVIEW: { count: 1, cents: 500 },
    REJECT: { count: 1, cents: 3125 },
  } },
}
const csv = (name: string) => new File(["column\nvalue\n"], name, { type: "text/csv" })
const failure = (status: number, code: string, details?: object, headers?: HeadersInit) =>
  Response.json({ error: { code, details } }, { status, headers })

function mount(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><App /></MemoryRouter></QueryClientProvider>)
  return client
}

function mockWorkspace(startSession: Session | null = admin, custom?: (url: string, options: RequestInit) =>
Response | Promise<Response> | undefined) {
  let user = startSession
  const fetcher = vi.fn(async (url: string, options: RequestInit = {}) => {
    const result = custom?.(url, options)
    if (result) return await result
    if (url === "/api/auth/me") return user ? Response.json(user) : failure(401, "unauthorized")
    if (url === "/api/auth/csrf") return Response.json({ csrf_token: user ? "bound" : "anonymous" })
    if (url === "/api/auth/login") { user = admin; return Response.json(user) }
    if (url === "/api/auth/logout") { user = null; return new Response(null, { status: 204 }) }
    if (url === "/api/datasets") return Response.json([batch])
    if (url === `/api/datasets/${batch.batch_id}`) return Response.json(batch)
    if (url.startsWith("/api/evaluations?")) return Response.json([run])
    if (url === `/api/evaluations/${run.run_id}`) return Response.json(run)
    throw new Error(`Unexpected request ${url}`)
  })
  vi.stubGlobal("fetch", fetcher)
  return fetcher
}

async function fillCredentials() {
  fireEvent.change(await screen.findByLabelText("Username"), { target: { value: "operator" } })
  fireEvent.change(screen.getByLabelText("Password", { exact: true }), { target: { value: "synthetic password" } })
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }))
}

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe("cookie authentication", () => {
  it("protects deep links and signs in using an anonymous CSRF bootstrap", async () => {
    const fetcher = mockWorkspace(null)
    mount("/evaluations")
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeTruthy()
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }))
    expect(screen.getByRole("alert").textContent).toContain("Enter your username and password.")
    fireEvent.click(screen.getByRole("button", { name: "Show password" }))
    expect(screen.getByLabelText("Password", { exact: true }).getAttribute("type")).toBe("text")
    await fillCredentials()
    expect(await screen.findByRole("heading", { name: "Evaluations" })).toBeTruthy()
    const login = fetcher.mock.calls.find(([url]) => url === "/api/auth/login")?.[1]
    expect(new Headers(login?.headers).get("X-CSRF-Token")).toBe("anonymous")
    expect(login?.credentials).toBe("same-origin")
  })

  it.each([
    [401, "The username or password is incorrect."],
    [429, "Too many sign-in attempts. Try again in 7 seconds."],
  ])("shows a safe sign-in error for %s", async (status, message) => {
    mockWorkspace(null, (url) => url === "/api/auth/login"
      ? failure(status, "login_error", undefined, { "Retry-After": "7" }) : undefined)
    mount("/")
    await fillCredentials()
    expect((await screen.findByRole("alert")).textContent).toContain(message)
  })

  it("restores a session and clears workspace caches on logout", async () => {
    const fetcher = mockWorkspace(admin)
    const client = mount("/datasets")
    await screen.findByRole("button", { name: "Inspect batch batch-ready" })
    expect(client.getQueriesData({ queryKey: ["workspace"] }).length).toBeGreaterThan(0)
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }))
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeTruthy()
    expect(client.getQueriesData({ queryKey: ["workspace"] })).toEqual([])
    expect(new Headers(fetcher.mock.calls.find(([url]) => url === "/api/auth/logout")?.[1]?.headers)
      .get("X-CSRF-Token")).toBe("bound")
  })

  it("redirects on an authenticated API 401 and removes scoped data", async () => {
    mockWorkspace(admin, (url) => url === "/api/datasets" ? failure(401, "unauthorized") : undefined)
    const client = mount("/datasets")
    expect(await screen.findByText("Your session expired. Sign in to continue.")).toBeTruthy()
    expect(client.getQueriesData({ queryKey: ["workspace"] })).toEqual([])
  })
})

describe("dataset workflows", () => {
  it("validates file choices and constructs exact typed multipart fields", () => {
    expect(requiredFiles("INITIAL")).toEqual(fileTypes)
    expect(requiredFiles("INCREMENTAL")).toEqual(["latest_transactions", "pending_payouts"])
    expect(fileProblem(new File([], "blank.csv"))).toContain("empty")
    expect(fileProblem(csv("notes.txt"))).toContain("CSV")
    expect(fileProblem(new File([new Uint8Array(4 * 1024 * 1024 + 1)], "large.csv"))).toContain("4 MB")
    const form = makeUpload("INITIAL", "2025-02-20T10:00", { teams: csv("teams.csv") }, true)
    expect([...form.keys()]).toEqual(["mode", "cutoff", "assessment", "teams"])
    expect(form.get("cutoff")).toBe("2025-02-20T10:00")
  })

  it("supports incremental uploads, sanitizes validation errors and invalidates the dataset list", async () => {
    let writes = 0
    const fetcher = mockWorkspace(admin, (url, options) => {
      if (url !== "/api/datasets/upload") return
      writes += 1
      const data = options.body as FormData
      expect([...data.keys()]).toEqual(["mode", "cutoff", "latest_transactions", "pending_payouts"])
      expect(data.get("mode")).toBe("INCREMENTAL")
      expect(new Headers(options.headers).has("Content-Type")).toBe(false)
      expect(new Headers(options.headers).get("X-CSRF-Token")).toBe("bound")
      return writes === 1 ? failure(422, "validation_error", {
        code: "invalid_value", file_type: "pending_payouts", row: 2, field: "amount_usd",
      }) : Response.json(batch, { status: 200 })
    })
    mount("/datasets")
    fireEvent.click(await screen.findByRole("button", { name: "Import dataset" }))
    expect(screen.getByLabelText("Teams")).toBeTruthy()
    fireEvent.change(screen.getByLabelText("Evaluation cutoff (IST)"), { target: { value: "2025-02-20T10:00" } })
    fireEvent.change(screen.getByLabelText("Latest transactions"), { target: { files: [csv("tx.csv")] } })
    fireEvent.change(screen.getByLabelText("Pending payouts"), { target: { files: [csv("payouts.csv")] } })
    expect((screen.getByRole("button", { name: "Upload dataset" }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.change(screen.getByLabelText("Import mode"), { target: { value: "INCREMENTAL" } })
    fireEvent.click(screen.getByRole("button", { name: "Upload dataset" }))
    expect(await screen.findByText("Pending payouts · Row 2 · Field amount_usd")).toBeTruthy()
    await waitFor(() => expect(fetcher.mock.calls.filter(([url]) => url === "/api/datasets").length).toBeGreaterThan(1))
    fireEvent.click(screen.getByRole("button", { name: "Retry upload" }))
    expect(await screen.findByText("Existing batch reused")).toBeTruthy()
    expect(await screen.findByRole("heading", { name: "Batch details" })).toBeTruthy()
    await waitFor(() => expect(fetcher.mock.calls.filter(([url]) => url === "/api/datasets").length).toBeGreaterThan(2))
  })

  it("uses allowlisted presets in demo mode and hides file upload", async () => {
    const user = { ...admin, mode: "demo" as const, permissions: [...admin.permissions, "import_synthetic_demo" as const] }
    const fetcher = mockWorkspace(user, (url) => url === "/api/datasets/demo-import"
      ? Response.json(batch, { status: 201 }) : undefined)
    mount("/datasets")
    fireEvent.click(await screen.findByRole("button", { name: "Import synthetic preset" }))
    expect(screen.getByText("Synthetic demo")).toBeTruthy()
    expect(screen.queryByLabelText("Teams")).toBeNull()
    fireEvent.click(screen.getByRole("button", { name: "Import preset" }))
    expect(await screen.findByText("Dataset imported")).toBeTruthy()
    const payload = fetcher.mock.calls.find(([url]) => url === "/api/datasets/demo-import")?.[1]?.body
    expect(payload).toBe(JSON.stringify({ preset_id: "synthetic-v1" }))
  })

  it.each(["ANALYST", "VIEWER"] as const)("shows %s history and details without import actions", async (role) => {
    const failed = { ...batch, batch_id: "failed", status: "FAILED",
      validation: { code: "invalid_value", file_type: "teams", row: 3, field: "team_id" } }
    const user = { ...admin, role, permissions: ["read_risk", "view_audit"] as Session["permissions"] }
    mockWorkspace(user, (url) => url === "/api/datasets" ? Response.json([batch, failed])
      : url === "/api/datasets/failed" ? Response.json(failed) : undefined)
    mount("/datasets")
    fireEvent.click(await screen.findByRole("button", { name: "Inspect batch failed" }))
    expect(await screen.findByText("Validation failed")).toBeTruthy()
    expect(screen.queryByRole("button", { name: "Import dataset" })).toBeNull()
    expect(screen.queryByRole("link", { name: "Use dataset for evaluation" })).toBeNull()
  })
})

describe("synchronous evaluations", () => {
  it("keeps the selected run stable, blocks duplicate starts and switches only on explicit selection", async () => {
    const older = { ...run, run_id: "run-old" }
    let resolve!: (response: Response) => void
    const pending = new Promise<Response>((done) => { resolve = done })
    const fetcher = mockWorkspace(admin, (url) => url === "/api/evaluations" ? pending
      : url === "/api/evaluations/run-old" ? Response.json(older) : undefined)
    mount("/evaluations?run_id=run-old&batch_id=batch-ready")
    const start = await screen.findByRole("button", { name: "Start evaluation" }) as HTMLButtonElement
    await waitFor(() => expect(start.disabled).toBe(false))
    fireEvent.submit(start.form!)
    fireEvent.submit(start.form!)
    expect(await screen.findByText("Waiting for the evaluation result.")).toBeTruthy()
    await waitFor(() => expect(fetcher.mock.calls.filter(([url]) => url === "/api/evaluations")).toHaveLength(1))
    await act(async () => resolve(Response.json(run, { status: 200 })))
    expect(await screen.findByText("Existing run reused")).toBeTruthy()
    await waitFor(() => expect(fetcher.mock.calls.filter(([url]) => url.startsWith("/api/evaluations?")).length)
      .toBeGreaterThan(1))
    expect(within(document.querySelector(".run-strip")!).getByText("run-old")).toBeTruthy()
    fireEvent.click(screen.getByRole("button", { name: "Inspect completed run" }))
    fireEvent.click(await screen.findByRole("button", { name: "Use this run" }))
    await waitFor(() => expect(within(document.querySelector(".run-strip")!).getByText("run-new")).toBeTruthy())
  })

  it("shows failures and only permits selecting completed runs", async () => {
    const failed = { ...run, run_id: "run-failed", status: "FAILED", failure: "processing_failed", summary: null }
    mockWorkspace(admin, (url) => {
      if (url === "/api/evaluations") return failure(503, "service_unavailable")
      if (url.startsWith("/api/evaluations?")) return Response.json([failed])
      if (url === "/api/evaluations/run-failed") return Response.json(failed)
    })
    mount("/evaluations?batch_id=batch-ready")
    fireEvent.click(await screen.findByRole("button", { name: "Inspect run run-failed" }))
    expect(await screen.findByText("Evaluation failed")).toBeTruthy()
    expect(screen.queryByRole("button", { name: "Use this run" })).toBeNull()
    const start = screen.getByRole("button", { name: "Start evaluation" }) as HTMLButtonElement
    await waitFor(() => expect(start.disabled).toBe(false))
    fireEvent.click(start)
    expect(await screen.findByText("The service is temporarily unavailable. Try again shortly.")).toBeTruthy()
    expect(screen.getByRole("button", { name: "Retry evaluation" })).toBeTruthy()
  })

  it("blocks evaluation of a non-ready batch even when explicitly named in the URL", async () => {
    const validating = { ...batch, batch_id: "batch-validating", status: "VALIDATING" }
    const fetcher = mockWorkspace(admin, (url) => url === "/api/datasets"
      ? Response.json([validating]) : undefined)
    mount("/evaluations?batch_id=batch-validating")
    const option = await screen.findByRole("option", { name: /batch-validating/ }) as HTMLOptionElement
    expect(option.disabled).toBe(true)
    const start = screen.getByRole("button", { name: "Start evaluation" }) as HTMLButtonElement
    expect(start.disabled).toBe(true)
    fireEvent.submit(start.form!)
    expect(fetcher.mock.calls.some(([url]) => url === "/api/evaluations")).toBe(false)
  })
})
