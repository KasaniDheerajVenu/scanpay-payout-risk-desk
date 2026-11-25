import { Activity, ArrowUpRight, Layers3 } from "lucide-react"
import { useQuery } from "@tanstack/react-query"
import { lazy, Suspense, useState } from "react"
import { Link, useSearchParams } from "react-router"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { EmptyPanel, LoadingPanel, PageHeader, RetryNotice, StatusBadge } from "@/components/ui/workspace"
import { hasPermission, useWorkspace } from "@/features/auth/session"
import { contextSearch, useRunSelection } from "@/features/evaluations/selection"
import { CompletedRun, ExportButton, mlContext, useRiskQuery } from "@/features/risk/shared"
import { formatCents, formatDate, formatIST, formatNumber } from "@/lib/format"
import { errorMessage } from "@/services/api"
import { getDashboard, type DecisionCategory, type Historical } from "@/services/risk"
import { batchKey, getBatches, getRuns, runKey } from "@/services/workspace"

const Charts = lazy(() => import("@/features/risk/dashboard-charts"))
const categories: { key: DecisionCategory; label: string; short: string; color: string }[] = [
  { key: "automatically_approved", label: "Automatically approved", short: "Auto", color: "var(--decision-auto)" },
  { key: "analyst_approved", label: "Analyst approved", short: "Analyst", color: "var(--decision-analyst)" },
  { key: "open_manual_review", label: "Open manual review", short: "Review", color: "var(--decision-review)" },
  { key: "rejected", label: "Rejected", short: "Reject", color: "var(--decision-reject)" },
]
const historicalLabels = [
  ["AUTO_APPROVED", "Automatic approval"], ["MANUAL_APPROVED", "Resolved analyst approval"], ["REJECTED", "Rejected"],
] as const

function DashboardSelection() {
  const { prefix } = useWorkspace()
  const [params, setParams] = useSearchParams()
  const { selected, selectedId, setSelected } = useRunSelection()
  const batchId = params.get("batch_id") ?? selected.data?.batch_id ?? ""
  const [offset, setOffset] = useState(0)
  const batches = useQuery({ queryKey: [...prefix, ...batchKey, "list"],
    queryFn: ({ signal }) => getBatches(signal), retry: false })
  const runs = useQuery({ queryKey: [...prefix, ...runKey, "dashboard-selector", batchId, offset],
    queryFn: ({ signal }) => getRuns(signal, offset, batchId), enabled: !!batchId, retry: false })
  const choices = (runs.data ?? []).filter((run) => run.status === "COMPLETED")
  const current = selected.data?.batch_id === batchId && selected.data?.status === "COMPLETED" ? selected.data : null
  if (current && !choices.some((run) => run.run_id === selectedId)) choices.unshift(current)
  return <Card className="content-card">
    <div className="form-grid">
      <label className="field"><span>Dataset</span><select value={batchId}
        disabled={batches.isPending} onChange={(event) => {
          const value = event.target.value
          setOffset(0)
          setParams((previous) => {
            const next = new URLSearchParams(previous)
            if (value) next.set("batch_id", value)
            else next.delete("batch_id")
            return next
          })
        }}>
        <option value="">Select a dataset</option>
        {batches.data?.filter((batch) => batch.status === "READY").map((batch) =>
          <option key={batch.batch_id} value={batch.batch_id}>{formatIST(batch.imported_at)} · {batch.batch_id}</option>)}
      </select></label>
      <label className="field"><span>Completed run</span><select value={current?.run_id ?? ""}
        disabled={!batchId || runs.isPending} onChange={(event) => {
          const run = choices.find((choice) => choice.run_id === event.target.value)
          if (run) setSelected(run)
        }}>
        <option value="">Select a completed run</option>
        {choices.map((run) => <option key={run.run_id} value={run.run_id}>
          {formatIST(run.completed_at)} · {run.run_id}
        </option>)}
      </select></label>
    </div>
    {batchId && runs.isSuccess && !choices.length && <p className="text-sm text-muted-foreground">No completed runs on this page.</p>}
    {batchId && (offset > 0 || runs.data?.length === 20) && <div className="flex flex-wrap gap-2">
      <Button variant="secondary" disabled={!offset} onClick={() => setOffset(offset - 20)}>Newer runs</Button>
      <Button variant="secondary" disabled={runs.data?.length !== 20} onClick={() => setOffset(offset + 20)}>Older runs</Button>
    </div>}
    {batches.isError && <RetryNotice message={errorMessage(batches.error)} onRetry={() => void batches.refetch()} />}
    {runs.isError && <RetryNotice message={errorMessage(runs.error)} onRetry={() => void runs.refetch()} />}
  </Card>
}

function HistoricalDetails({ history }: { history: Historical }) {
  const countChange = history.current_vs_daily_average_count_percent
  const amountChange = history.current_vs_daily_average_cents_percent
  return <Card className="content-card">
    <div className="section-heading"><h2>Historical comparison</h2>
      <span className="section-count">{formatDate(history.first_day)} – {formatDate(history.last_day)} IST</span></div>
    <p className="text-sm text-muted-foreground">
      {history.calendar_days} calendar days · {history.denominator_days} recorded days used for averages · {history.unknown_days} days with unknown coverage
    </p>
    <div className="form-grid">
      <div><strong className="text-sm">Average daily payout count</strong>
        <p>{history.daily_average_count == null ? "Unavailable" : history.daily_average_count}</p>
        {countChange != null && <p className="text-xs text-muted-foreground">Current vs. daily average {countChange}%</p>}
      </div>
      <div><strong className="text-sm">Average daily payout amount</strong>
        <p>{history.daily_average_cents == null ? "Unavailable" : formatCents(Number(history.daily_average_cents))}</p>
        {amountChange != null && <p className="text-xs text-muted-foreground">Current vs. daily average {amountChange}%</p>}
      </div>
    </div>
    <details className="risk-disclosure">
      <summary>Daily historical counts and amounts</summary>
      <div className="table-scroll mt-3" role="region" aria-label="Historical counts and amounts" tabIndex={0}>
        <table className="risk-table">
          <thead><tr><th scope="col">Date (IST)</th>{historicalLabels.map(([key, label]) => <th scope="col" key={key}>{label}</th>)}</tr></thead>
          <tbody>{history.days.map((day) => <tr key={day.date_ist}>
            <th scope="row">{formatDate(day.date_ist)}{day.coverage === "no_payout_scheduled" && <small className="block">Non-settlement day</small>}</th>
            {day.totals ? historicalLabels.map(([key]) => <td key={key}>{day.totals![key].count} · {formatCents(day.totals![key].cents)}</td>)
              : <td colSpan={3}>Unknown coverage</td>}
          </tr>)}</tbody>
        </table>
      </div>
    </details>
  </Card>
}

function DashboardResults({ runId }: { runId: string }) {
  const { user } = useWorkspace()
  const [params] = useSearchParams()
  const dashboard = useRiskQuery(runId, ["dashboard"], (signal) => getDashboard(runId, signal))
  if (dashboard.isPending) return <LoadingPanel label="Loading dashboard" />
  if (dashboard.isError) return <RetryNotice message={errorMessage(dashboard.error)}
    onRetry={() => void dashboard.refetch()} />
  const { total, categories: totals, historical, ml, fraud_clusters: clusters, evaluation } = dashboard.data
  const context = contextSearch(params)
  const gates = ml?.metadata.quality_gates ?? []
  return <>
    <div className="risk-context">
      <dl className="metadata-grid"><div><dt>Run ID</dt><dd><code>{evaluation.run_id}</code></dd></div>
        <div><dt>Dataset</dt><dd><code>{evaluation.batch_id}</code></dd></div>
        <div><dt>Evaluation cutoff</dt><dd>{formatIST(evaluation.cutoff_at)}</dd></div>
        <div><dt>Snapshot</dt><dd>{formatIST(evaluation.snapshot_at)}</dd></div></dl>
      {hasPermission(user, "export_decisions") && <ExportButton runId={runId} />}
    </div>
    <div className="risk-tiles">
      {[{ label: "Total payouts", total, color: "var(--primary)" },
        ...categories.map((category) => ({ ...category, total: totals[category.key] }))].map((tile) =>
        <Card key={tile.label} className="risk-tile">
          <div><span className="metric-dot" style={{ background: tile.color }} />{tile.label}</div>
          <strong>{formatCents(tile.total.cents)}</strong><small>{formatNumber(tile.total.count)} payouts</small>
        </Card>)}
    </div>
    <Suspense fallback={<LoadingPanel label="Loading risk charts" />}>
      <Charts distribution={categories.map((category) => ({ ...category, count: totals[category.key].count }))}
        history={historical} />
    </Suspense>
    {historical && <HistoricalDetails history={historical} />}
    <div className="form-grid">
      <Card className="content-card">
        <div className="section-heading"><h2>Fraud clusters</h2>
          <Link to={"/fraud" + context} className="text-link inline-flex gap-1 text-sm">Inspect <ArrowUpRight className="size-4" aria-hidden="true" /></Link></div>
        {clusters.length === 0 ? <EmptyPanel icon={Layers3} title="No published clusters">This run has no known fraud clusters.</EmptyPanel>
          : <ul className="grid gap-3">{clusters.map((cluster) => <li key={cluster.cluster_id} className="risk-nested">
            <strong className="text-sm">{cluster.reason_code.replaceAll("_", " ")}</strong>
            <p className="text-sm text-muted-foreground">{cluster.summary}</p>
          </li>)}</ul>}
      </Card>
      <Card className="content-card">
        <div className="section-heading"><h2>Model quality</h2>
          <Link to={"/models" + context} className="text-link inline-flex gap-1 text-sm">Inspect <ArrowUpRight className="size-4" aria-hidden="true" /></Link></div>
        {ml ? <><StatusBadge status={ml.status} /><p className="text-sm">{mlContext[ml.status]}</p>
          {gates.length > 0 && <p className="text-sm text-muted-foreground">
            {gates.filter((gate) => gate.passed).length} of {gates.length} published quality gates passed
          </p>}</> : <EmptyPanel icon={Activity} title="ML metadata unavailable">No model summary was persisted for this run.</EmptyPanel>}
      </Card>
    </div>
  </>
}

export function DashboardPage() {
  return <>
    <PageHeader title="Risk overview" description="Monitor payout decisions and historical activity for a completed evaluation." />
    <DashboardSelection />
    <CompletedRun>{(run) => <DashboardResults key={run.run_id} runId={run.run_id} />}</CompletedRun>
  </>
}
