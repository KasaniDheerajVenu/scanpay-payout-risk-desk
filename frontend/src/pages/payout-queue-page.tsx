import { ArrowUpRight, ChevronLeft, ChevronRight, ListChecks, Search } from "lucide-react"
import { useLayoutEffect, useRef, useState, type FormEvent } from "react"
import { Link, useSearchParams } from "react-router"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { EmptyPanel, LoadingPanel, PageHeader, RetryNotice, StatusBadge } from "@/components/ui/workspace"
import { hasPermission, useWorkspace } from "@/features/auth/session"
import { contextSearch } from "@/features/evaluations/selection"
import { CompletedRun, ExportButton, useRiskQuery } from "@/features/risk/shared"
import { formatCents, formatDate, formatNumber, labelOf } from "@/lib/format"
import { errorMessage } from "@/services/api"
import { getQueue } from "@/services/risk"

const pageSize = 20
const decisions = ["APPROVE", "MANUAL_REVIEW", "REJECT"]
const sorts = { payout_id: "Payout ID", amount: "Payout amount", team_name: "Team name", created_at: "Created date" }

function SearchControl({ search, onSearch }: { search: string; onSearch: (value: string) => void }) {
  const [draft, setDraft] = useState(search)
  function submit(event: FormEvent) {
    event.preventDefault()
    onSearch(draft.trim())
  }
  return <form className="queue-search" onSubmit={submit}>
    <label className="field"><span>Search payouts</span>
      <input type="search" value={draft} placeholder="Payout, team or profession" maxLength={128}
        onChange={(event) => setDraft(event.target.value)} />
    </label>
    <Button type="submit" variant="secondary"><Search className="size-4" aria-hidden="true" /> Search</Button>
  </form>
}

function QueueResults({ runId }: { runId: string }) {
  const { user } = useWorkspace()
  const [params, setParams] = useSearchParams()
  const latestParams = useRef(params)
  useLayoutEffect(() => { latestParams.current = params }, [params])
  const search = (params.get("search") ?? "").slice(0, 128)
  const decisionValue = params.get("decision") ?? ""
  const decision = decisions.includes(decisionValue) ? decisionValue : ""
  const sortValue = params.get("sort") ?? ""
  const sort = Object.hasOwn(sorts, sortValue) ? sortValue : "payout_id"
  const direction = params.get("direction") === "desc" ? "desc" : "asc"
  const offsetValue = Number(params.get("offset"))
  const offset = Number.isSafeInteger(offsetValue) && offsetValue >= 0 && offsetValue <= 100_000 ? offsetValue : 0
  const filters: Record<string, string> = { sort, direction, offset: String(offset), limit: String(pageSize) }
  if (search) filters.search = search
  if (decision) filters.decision = decision
  const queue = useRiskQuery(runId, ["queue", filters], (signal) => getQueue(runId, filters, signal))
  function change(key: string, value: string) {
    // Router navigations can render after another filter change has arrived.
    const next = new URLSearchParams(latestParams.current)
    if (key !== "offset") next.delete("offset")
    if (value) next.set(key, value)
    else next.delete(key)
    latestParams.current = next
    setParams(next)
  }
  const data = queue.data
  return <>
    <Card className="content-card">
      <div className="queue-controls">
        <SearchControl key={search} search={search} onSearch={(value) => change("search", value)} />
        <label className="field">Effective decision<select value={decision} onChange={(event) => change("decision", event.target.value)}>
          <option value="">All decisions</option>
          {decisions.map((value) => <option key={value} value={value}>{labelOf(value)}</option>)}
        </select></label>
        <label className="field">Sort by<select value={sort} onChange={(event) => change("sort", event.target.value)}>
          {Object.entries(sorts).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </select></label>
        <label className="field">Order<select value={direction} onChange={(event) => change("direction", event.target.value)}>
          <option value="asc">Ascending</option><option value="desc">Descending</option>
        </select></label>
      </div>
    </Card>
    {queue.isPending ? <LoadingPanel label="Loading payouts" /> : queue.isError
      ? <RetryNotice message={errorMessage(queue.error)} onRetry={() => void queue.refetch()} />
      : data && <Card className="content-card">
        <div className="section-heading"><h2>Payouts <span className="section-count ml-2">{formatNumber(data.total)} matching</span></h2>
          {hasPermission(user, "export_decisions") && <ExportButton runId={runId} />}</div>
        {data.items.length === 0 ? <EmptyPanel icon={ListChecks} title="No matching payouts">
          Adjust the filters or choose another run.
        </EmptyPanel> : <div className="table-scroll" role="region" aria-label="Payout queue table" tabIndex={0}>
          <table className="risk-table payout-table">
            <thead><tr>{["Payout", "Team", "Amount", "Captured", "Coverage (IST)", "Original decision", "Effective decision", "Source", "Fraud cluster", "ML status", "Investigation"].map((column) =>
              <th scope="col" key={column}>{column}</th>)}</tr></thead>
            <tbody>{data.items.map((payout) => {
              const detail = `/payouts/${encodeURIComponent(payout.payout_id)}?${params}`
              return <tr key={payout.payout_id}>
                <th scope="row"><Link className="text-link" to={detail}>{payout.payout_id}</Link></th>
                <td>{payout.team_name}<small className="block">{payout.team_id} · {payout.profession}</small></td>
                <td className="whitespace-nowrap font-semibold tabular-nums">{formatCents(payout.payout_amount_cents)}</td>
                <td>{formatNumber(payout.captured_count)}</td>
                <td>{formatDate(payout.covers_from)}<small className="block">to {formatDate(payout.covers_to)}</small></td>
                <td><StatusBadge status={payout.original_decision} /></td>
                <td><StatusBadge status={payout.effective_decision} /></td>
                <td>{labelOf(payout.decision_source)}<small className="block">Version {payout.effective_version}</small></td>
                <td>{payout.cluster_id ? <Link className="text-link" title={payout.cluster_id}
                  to={"/fraud" + contextSearch(params) + "#" + payout.cluster_id}>{payout.cluster_id.slice(0, 8)}</Link> : "—"}</td>
                <td><StatusBadge status={payout.ml_status} />{payout.ml_is_anomaly === true && <small className="block">Anomaly flagged</small>}</td>
                <td><Link to={detail} className="text-link inline-flex gap-1">Investigate <ArrowUpRight className="size-4" aria-hidden="true" /></Link></td>
              </tr>
            })}</tbody>
          </table>
        </div>}
        <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
          <span className="text-muted-foreground">{data.items.length ? `${data.offset + 1}–${data.offset + data.items.length} of ${data.total}` : `0 of ${data.total}`}</span>
          <div className="flex gap-2">
            <Button variant="secondary" disabled={offset === 0} onClick={() => change("offset", String(Math.max(0, offset - pageSize)))}>
              <ChevronLeft className="size-4" aria-hidden="true" /> Previous</Button>
            <Button variant="secondary" disabled={offset + pageSize >= data.total || offset + pageSize > 100_000}
              onClick={() => change("offset", String(offset + pageSize))}>Next <ChevronRight className="size-4" aria-hidden="true" /></Button>
          </div>
        </div>
      </Card>}
  </>
}

export function PayoutQueuePage() {
  return <>
    <PageHeader title="Payout queue" description="Find payouts and inspect the evidence behind their effective decisions." />
    <CompletedRun>{(run) => <QueueResults key={run.run_id} runId={run.run_id} />}</CompletedRun>
  </>
}
