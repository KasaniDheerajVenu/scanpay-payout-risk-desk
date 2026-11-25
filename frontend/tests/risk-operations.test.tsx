import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { MemoryRouter } from "react-router"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { App } from "@/App"
import type { Audit, Cluster, Dashboard, Finding, Investigation, Model, Override, Payout } from "@/services/risk"
import type { Evaluation, Session } from "@/services/workspace"

vi.mock("@/features/risk/dashboard-charts", () => ({
  default: ({ distribution, history }: { distribution: { count: number }[]; history: Dashboard["historical"] }) =>
    <div aria-label="Chart data">{distribution.map((entry) => entry.count).join(",")};{history?.days[1].totals === null ? "gap" : "known"}</div>,
}))

const date = "2025-02-20T04:30:00Z"
const payoutId = "000401"
const reviewer: Session = { user_id: "reviewer", username: "analyst", display_name: "Synthetic analyst",
  role: "ANALYST", mode: "local", permissions: ["read_risk", "view_audit", "override_decisions", "export_decisions"] }
const viewer: Session = { ...reviewer, user_id: "reader", role: "VIEWER", permissions: ["read_risk", "view_audit"] }
const model: Model = {
  model_version_id: "model-1", algorithm: "isolation_forest", feature_schema_version: "feature-v1",
  status: "DIAGNOSTIC_ONLY", fit_cutoff_at: date, calibration_cutoff_at: date, validation_cutoff_at: date,
  created_at: date, training_observation_count: 30, validation_observation_count: 12,
  anomaly_threshold: .59, metrics: { validation_scored: 12, validation_anomaly_rate: .25 },
  quality_gates: [{ code: "review_workload", passed: false, observed: .25, limit: .2 }],
}
const run: Evaluation = { run_id: "newer", batch_id: "batch-one", cutoff_at: date, created_at: date, started_at: date,
  completed_at: date, status: "COMPLETED", rule_version: "hybrid-v1", failure: null, summary: null, model }
const originalAudit: Audit = {
  effective: { payout_id: payoutId, run_id: "newer", decision_id: "decision-1", original_decision: "REJECT",
    effective_decision: "REJECT", effective_version: 1, decision_source: "AUTOMATED", cluster_id: "cluster-1",
    reason: "Published exposure", evidence: [{ type: "transaction", id: "000301" }],
    actor: null, history_id: null, timestamp: date },
  history: [{ version: 1, previous_decision: null, new_decision: "REJECT", status: "CURRENT",
    decision_source: "AUTOMATED", cluster_id: "cluster-1", reason: "Published exposure", evidence: [],
    actor: null, history_id: null, timestamp: date }],
}
const path = { seed_payout_id: "000202", steps: [{
  start: { kind: "transaction", source_id: "000102" }, end: { kind: "transaction", source_id: "000301" },
  relationship: "captured_payment", strength: "strong", supports_exposure: true, evidence: [{ type: "payout" as const, id: "000202" }],
}] }
const cluster: Cluster = { cluster_id: "cluster-1", stable_case_key: "case-a", seed_payout_id: "000202",
  reason_code: "FRAUD_CARD_RING", reason_codes: ["FRAUD_CARD_RING"], summary: "Synthetic case summary",
  evidence: [{ type: "payout", id: "000202" }],
  associated_payouts: [{ payout_id: payoutId, original_cluster: true, effective_cluster: true, paths: [path] }] }
const payout: Payout = { payout_id: payoutId, team_id: "0001", team_name: "Synthetic merchant", profession: "Plumbing",
  team_status: "active", payout_amount_cents: 3125, captured_count: 2, covers_from: "2025-02-15",
  covers_to: "2025-02-17", created_at: date, original_decision: "REJECT", effective_decision: "REJECT",
  decision_source: "AUTOMATED", effective_version: 1, reason: "Published exposure", cluster_id: "cluster-1",
  ml_status: "DIAGNOSTIC_ONLY", ml_is_anomaly: true }
const finding: Finding = {
  feature: "payout_cents", unit: "cents", observed: 3125, status: "anomaly", observation_availability: "available",
  observed_denominator: 2, explanation: "Above the published upper fence", lower_fence: "950", upper_fence: "2600",
  is_material: true, minimum_absolute: "500", minimum_relative: ".1", minimum_denominator: 2, iqr_multiplier: "1.5",
  current_transaction_ids: ["000301"], baseline: { reliability: "available", scope: "TEAM", sample_count: 7,
    peer_team_count: null, q1: "1500", median: "1800", q3: "2100", iqr: "600", payout_ids: ["000202"], transaction_ids: ["000102"] },
}
const dashboard: Dashboard = {
  evaluation: { run_id: "newer", batch_id: "batch-one", cutoff_at: date, snapshot_at: date, model_version_id: "model-1" },
  total: { count: 2, cents: 3625 },
  categories: { automatically_approved: { count: 0, cents: 0 }, analyst_approved: { count: 0, cents: 0 },
    open_manual_review: { count: 1, cents: 500 }, rejected: { count: 1, cents: 3125 } },
  historical: { first_day: "2025-01-30", last_day: "2025-02-18", calendar_days: 20, recorded_days: 1,
    non_settlement_days: 0, unknown_days: 19, denominator_days: 1, daily_average_count: "1", daily_average_cents: "2000",
    current_vs_daily_average_count_percent: "100", current_vs_daily_average_cents_percent: null,
    totals: { AUTO_APPROVED: { count: 0, cents: 0 }, MANUAL_APPROVED: { count: 1, cents: 2000 }, REJECTED: { count: 0, cents: 0 } },
    days: [{ date_ist: "2025-02-01", coverage: "recorded",
      totals: { AUTO_APPROVED: { count: 0, cents: 0 }, MANUAL_APPROVED: { count: 1, cents: 2000 }, REJECTED: { count: 0, cents: 0 } } },
    { date_ist: "2025-02-02", coverage: "unknown_source_coverage", totals: null }] },
  ml: { status: "DIAGNOSTIC_ONLY", payout_status_counts: { DIAGNOSTIC_ONLY: 2 }, metadata: model },
  fraud_clusters: [cluster],
}

type ServerOptions = {
  user?: Session; stale?: boolean; initialDecision?: "REJECT" | "MANUAL_REVIEW"
  intercept?: (url: URL) => Response | undefined
}

function server({ user = reviewer, stale = false, initialDecision, intercept }: ServerOptions = {}) {
  const audit = structuredClone(originalAudit)
  if (initialDecision) audit.effective.effective_decision = initialDecision
  const seen: { url: URL; init: RequestInit }[] = []
  const writes: Override[] = []
  const request = vi.fn(async (target: string, init: RequestInit) => {
    const url = new URL(target, "http://test.local")
    seen.push({ url, init })
    const response = intercept?.(url)
    if (response) return response
    if (url.pathname === "/api/auth/me") return Response.json(user)
    if (url.pathname === "/api/auth/csrf") return Response.json({ csrf_token: "session-csrf" })
    if (url.pathname === "/api/datasets") return Response.json([{ batch_id: "batch-one", status: "READY", imported_at: date }])
    if (url.pathname === "/api/evaluations") return Response.json([run, { ...run, run_id: "older" }])
    if (url.pathname.startsWith("/api/evaluations/")) return Response.json({ ...run, run_id: url.pathname.split("/").at(-1) })
    if (url.pathname === "/api/dashboard") {
      const runId = url.searchParams.get("run_id")!
      return Response.json({ ...dashboard, evaluation: { ...dashboard.evaluation, run_id: runId } })
    }
    if (url.pathname === "/api/payouts") return Response.json({ items: [payout], total: 42, limit: 20, offset: Number(url.searchParams.get("offset")) })
    if (url.pathname.endsWith("/history")) return Response.json(audit)
    if (url.pathname.endsWith("/overrides")) {
      const payload = JSON.parse(String(init.body)) as Override
      writes.push(payload)
      const updated = { ...audit.effective, effective_decision: payload.decision, effective_version: 2, reason: payload.reason,
        decision_source: "HUMAN" as const, actor: { display_name: user.display_name, user_id: user.user_id }, cluster_id: payload.cluster_id }
      audit.effective = updated
      audit.history[0].status = "SUPERSEDED"
      audit.history.push({ ...updated, version: 2, previous_decision: "REJECT", new_decision: payload.decision, status: "CURRENT" })
      return stale ? Response.json({ error: { code: "stale_decision_version" } }, { status: 409 }) : Response.json(updated, { status: 201 })
    }
    if (url.pathname === `/api/payouts/${payoutId}`) {
      const detail: Investigation = {
        payout, decisions: audit, transactions: [
          { txn_id: "000301", created_at: date, collection_date_ist: "2025-02-20", amount_cents: 2500, tip_cents: 625, status: "captured", payment_method: "card" },
          { txn_id: "000302", created_at: date, collection_date_ist: "2025-02-20", amount_cents: 100, tip_cents: 0, status: "declined", payment_method: "ach" },
        ],
        statistics: [{ team: finding, profession: { ...finding, baseline: { ...finding.baseline, scope: "PROFESSION" } }, preferred_scope: "TEAM" }],
        fraud: { status: "established_exposure", established_exposure: true, explanation: "Established historical exposure", current_evidence: [],
          primary: { stable_case_key: "case-a", paths: [path], current_evidence: [], historical_evidence: [], relationship: "case_link",
            strength: "strong", supporting_seed_ids: ["000202"] }, alternatives: [], leads: [] },
        ml: { status: "DIAGNOSTIC_ONLY", anomaly_score: .61, anomaly_threshold: .59, is_anomaly: true, observed_model_features: 8,
          retained_model_features: 6, qualifies_for_review: false, diagnostic_code: "workload", explanation: "Diagnostic evidence only",
          evidence: [], unusual_features: [], feature_availability: [] },
      }
      return Response.json(detail)
    }
    if (url.pathname === "/api/fraud-clusters") return Response.json([cluster])
    if (url.pathname === "/api/models") return Response.json([model])
    if (url.pathname === "/api/exports/decisions.csv") return new Response("payout_id,team_id,decision,fraud_cluster,reasons,evidence\n", { headers: { "content-type": "text/csv" } })
    throw Error("Unexpected API path")
  })
  vi.stubGlobal("fetch", request)
  return { seen, writes }
}

function mount(path: string) {
  const cache = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } })
  render(<QueryClientProvider client={cache}><MemoryRouter initialEntries={[path]}><App /></MemoryRouter></QueryClientProvider>)
  return cache
}
const payoutUrl = `/payouts/${payoutId}?run_id=newer`

beforeEach(() => {
  HTMLDialogElement.prototype.showModal = function () { this.open = true }
  HTMLDialogElement.prototype.close = function () { this.open = false; this.dispatchEvent(new Event("close")) }
})
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

describe("persisted risk results", () => {
  it("renders recorded totals and history gaps, and reads an older run until another is selected", async () => {
    const { seen } = server()
    mount("/dashboard?run_id=older")
    expect(await screen.findByText("$36.25")).toBeTruthy()
    expect(screen.getByText("Resolved analyst approval")).toBeTruthy()
    expect(screen.getByText("Unknown coverage")).toBeTruthy()
    const chart = await screen.findByLabelText("Chart data")
    expect(chart.textContent).toBe("0,0,1,1;gap")
    expect(seen.some(({ url }) => url.pathname === "/api/dashboard" && url.searchParams.get("run_id") === "older")).toBe(true)
    await screen.findByRole("option", { name: /newer/ })
    fireEvent.change(screen.getByLabelText("Completed run"), { target: { value: "newer" } })
    await waitFor(() => expect(seen.some(({ url }) => url.pathname === "/api/dashboard" && url.searchParams.get("run_id") === "newer")).toBe(true))
  })

  it("passes search, decision, sorting and pagination to the API and preserves queue parameters in the detail link", async () => {
    const { seen } = server()
    mount("/payouts?run_id=newer")
    expect(await screen.findByText("42 matching")).toBeTruthy()
    fireEvent.change(screen.getByLabelText("Search payouts"), { target: { value: "plumbing" } })
    fireEvent.click(screen.getByRole("button", { name: "Search" }))
    fireEvent.change(screen.getByLabelText("Effective decision"), { target: { value: "REJECT" } })
    fireEvent.change(screen.getByLabelText("Sort by"), { target: { value: "amount" } })
    fireEvent.change(screen.getByLabelText("Order"), { target: { value: "desc" } })
    fireEvent.click(await screen.findByRole("button", { name: "Next" }))
    await waitFor(() => expect(seen.some(({ url }) => url.pathname === "/api/payouts" &&
      ["search:plumbing", "decision:REJECT", "sort:amount", "direction:desc", "offset:20", "run_id:newer"].every((item) => {
        const [key, value] = item.split(":")
        return url.searchParams.get(key) === value
      }))).toBe(true))
    const link = await screen.findByRole("link", { name: "Investigate" })
    expect(link.getAttribute("href")).toContain("search=plumbing")
    expect(link.getAttribute("href")).toContain("offset=20")
  })

  it("keeps the sort field when the direction changes before navigation is rendered", async () => {
    const { seen } = server()
    mount("/payouts?run_id=newer")
    await screen.findByText("42 matching")
    act(() => {
      fireEvent.change(screen.getByLabelText("Sort by"), { target: { value: "amount" } })
      fireEvent.change(screen.getByLabelText("Order"), { target: { value: "desc" } })
    })
    await waitFor(() => expect(seen.some(({ url }) =>
      url.pathname === "/api/payouts" && url.searchParams.get("sort") === "amount" &&
      url.searchParams.get("direction") === "desc")).toBe(true))
  })

  it("renders actual investigation finding shapes, with read-only controls for viewers", async () => {
    server({ user: viewer })
    mount(payoutUrl)
    expect(await screen.findByRole("heading", { name: "Statistical analysis" })).toBeTruthy()
    expect(screen.getByText("Team baseline")).toBeTruthy()
    expect(screen.getByText("Profession baseline")).toBeTruthy()
    expect(screen.getByText("Established historical exposure")).toBeTruthy()
    expect(screen.getByText("Diagnostic evidence only")).toBeTruthy()
    expect(screen.getByText("Declined")).toBeTruthy()
    expect(screen.getByText("Version 1 · Automated")).toBeTruthy()
    expect(screen.queryByRole("button", { name: "Override decision" })).toBeNull()
  })

  it("renders actual model gates and fraud relationships", async () => {
    server()
    mount("/models?run_id=newer")
    expect(await screen.findByRole("heading", { name: "Selected-run model" })).toBeTruthy()
    expect(screen.getAllByText("Review Workload").length).toBeGreaterThan(0)
    expect(screen.getAllByText("25%").length).toBeGreaterThan(0)
    fireEvent.click(screen.getByRole("link", { name: "Fraud insights" }))
    expect(await screen.findByText("Synthetic case summary")).toBeTruthy()
    expect(screen.getByText(/Evidence path from 000202/)).toBeTruthy()
  })
})

describe("analyst operations", () => {
  async function fillOverride(decision: string) {
    fireEvent.click(await screen.findByRole("button", { name: "Override decision" }))
    const dialog = screen.getByRole("dialog")
    if (decision === "REJECT") await waitFor(() => {
      expect((within(dialog).getByRole("option", { name: "Reject" }) as HTMLOptionElement).disabled).toBe(false)
    })
    fireEvent.change(within(dialog).getByLabelText("New decision"), { target: { value: decision } })
    fireEvent.change(within(dialog).getByLabelText("Override reason"), { target: { value: "Reviewed the evidence." } })
    return dialog
  }

  it("confirms an override, includes the expected version and invalidates other run views", async () => {
    const { seen, writes } = server()
    const cache = mount(payoutUrl)
    const dashboardKey = ["workspace", reviewer.user_id, "risk", "newer", "dashboard"]
    cache.setQueryData(dashboardKey, dashboard)
    const dialog = await fillOverride("MANUAL_REVIEW")
    fireEvent.click(within(dialog).getByRole("button", { name: "Review change" }))
    expect(within(dialog).getByText("Select a supporting transaction or historical payout.")).toBeTruthy()
    fireEvent.click(within(dialog).getByRole("checkbox", { name: "transaction 000301" }))
    fireEvent.click(within(dialog).getByRole("button", { name: "Review change" }))
    fireEvent.click(within(dialog).getByRole("button", { name: "Confirm override" }))
    expect(await screen.findByText("Override recorded")).toBeTruthy()
    expect(screen.getByText("Version 2 is current")).toBeTruthy()
    expect(writes).toEqual([{ run_id: "newer", expected_version: 1, decision: "MANUAL_REVIEW",
      reason: "Reviewed the evidence.", evidence: [{ type: "transaction", id: "000301" }], cluster_id: null }])
    expect(new Headers(seen.find(({ url }) => url.pathname.endsWith("/overrides"))?.init.headers).get("X-CSRF-Token")).toBe("session-csrf")
    expect(cache.getQueryState(dashboardKey)?.isInvalidated).toBe(true)
  })

  it("submits the selected Reject cluster with records from its published path", async () => {
    const { writes } = server({ initialDecision: "MANUAL_REVIEW" })
    mount(payoutUrl)
    const dialog = await fillOverride("REJECT")
    await within(dialog).findByRole("option", { name: "case-a" })
    fireEvent.change(within(dialog).getByLabelText("Same-run fraud cluster"), { target: { value: "cluster-1" } })
    fireEvent.click(within(dialog).getByRole("button", { name: "Review change" }))
    fireEvent.click(within(dialog).getByRole("button", { name: "Confirm override" }))
    await screen.findByText("Override recorded")
    expect(writes[0]).toMatchObject({ decision: "REJECT", cluster_id: "cluster-1",
      evidence: [{ type: "transaction", id: "000301" }, { type: "transaction", id: "000102" }, { type: "payout", id: "000202" }] })
  })

  it("refreshes the decision after 409 and requires reopening without retrying", async () => {
    const { writes, seen } = server({ stale: true })
    const cache = mount(payoutUrl)
    const dialog = await fillOverride("APPROVE")
    await act(async () => {
      cache.setQueryData(["workspace", reviewer.user_id, "risk", "newer", "history", payoutId], {
        ...originalAudit, effective: { ...originalAudit.effective, effective_version: 2 },
      })
    })
    fireEvent.click(within(dialog).getByRole("button", { name: "Review change" }))
    fireEvent.click(within(dialog).getByRole("button", { name: "Confirm override" }))
    expect(await screen.findByText("Decision changed")).toBeTruthy()
    expect(screen.queryByRole("dialog")).toBeNull()
    expect(screen.getByText("Version 2 is current")).toBeTruthy()
    expect(writes).toHaveLength(1)
    expect(writes[0].expected_version).toBe(1)
    expect(seen.filter(({ url }) => url.pathname.endsWith("/history")).length).toBeGreaterThan(1)
  })

  it("downloads the backend CSV and hides export for viewers", async () => {
    const { seen } = server()
    const createUrl = vi.fn(() => "blob:synthetic")
    vi.stubGlobal("URL", class extends URL {
      static createObjectURL = createUrl
      static revokeObjectURL = vi.fn()
    })
    const clicked = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      expect(this.download).toBe("decisions.csv")
    })
    mount("/dashboard?run_id=newer")
    fireEvent.click(await screen.findByRole("button", { name: "Export decisions" }))
    expect(await screen.findByText("CSV download started.")).toBeTruthy()
    expect(clicked).toHaveBeenCalledOnce()
    expect(seen.some(({ url }) => url.pathname === "/api/exports/decisions.csv" && url.search === "?run_id=newer")).toBe(true)
    cleanup()
    server({ user: viewer })
    mount("/dashboard?run_id=newer")
    await screen.findByText("$36.25")
    expect(screen.queryByRole("button", { name: "Export decisions" })).toBeNull()
  })

  it("shows an actionable error and an honest empty queue after retry", async () => {
    let attempt = 0
    server({ intercept: (url) => {
      if (url.pathname !== "/api/payouts") return
      return ++attempt === 1 ? Response.json({ error: { code: "unavailable" } }, { status: 503 })
        : Response.json({ items: [], limit: 20, offset: 0, total: 0 })
    } })
    mount("/payouts?run_id=newer")
    expect(await screen.findByRole("alert")).toBeTruthy()
    fireEvent.click(screen.getByRole("button", { name: "Retry" }))
    expect(await screen.findByText("No matching payouts")).toBeTruthy()
  })
})
