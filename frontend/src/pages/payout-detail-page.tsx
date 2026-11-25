import { ArrowLeft, Plus } from "lucide-react"
import { useState } from "react"
import { Link, useParams, useSearchParams } from "react-router"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { LoadingPanel, Notice, PageHeader, RetryNotice, StatusBadge } from "@/components/ui/workspace"
import { hasPermission, useWorkspace } from "@/features/auth/session"
import { contextSearch } from "@/features/evaluations/selection"
import { AuditTimeline, FraudPanel, MLPanel, StatisticsPanel } from "@/features/risk/evidence-panels"
import { OverrideDialog } from "@/features/risk/override-dialog"
import { CompletedRun, Evidence, useRiskQuery } from "@/features/risk/shared"
import { formatCents, formatDate, formatIST, formatNumber, labelOf } from "@/lib/format"
import { errorMessage } from "@/services/api"
import { getHistory, getInvestigation } from "@/services/risk"

function InvestigationView({ payoutId, runId }: { payoutId: string; runId: string }) {
  const detail = useRiskQuery(runId, ["payout", payoutId], (signal) => getInvestigation(runId, payoutId, signal))
  if (detail.isPending) return <LoadingPanel label="Loading payout investigation" />
  if (detail.isError) return <RetryNotice message={errorMessage(detail.error)} onRetry={() => void detail.refetch()} />
  return <PublishedInvestigation detail={detail.data} runId={runId} payoutId={payoutId} />
}

type OverrideResult = "applied" | "stale" | null
function PublishedInvestigation({ detail, runId, payoutId }: {
  detail: Awaited<ReturnType<typeof getInvestigation>>
  runId: string; payoutId: string
}) {
  const { user } = useWorkspace()
  const [params] = useSearchParams()
  const [overrideOpen, setOverrideOpen] = useState(false)
  const [result, setResult] = useState<OverrideResult>(null)
  const canOverride = hasPermission(user, "override_decisions")
  const history = useRiskQuery(runId, ["history", payoutId], (signal) => getHistory(runId, payoutId, signal))
  const audit = history.data && history.data.effective.effective_version >= detail.decisions.effective.effective_version
    ? history.data : detail.decisions
  const effective = audit.effective
  const original = detail.decisions.history.find((event) => event.version === 1)
  const { payout, transactions, statistics, fraud, ml } = detail
  return <>
    {result === "applied" && <Notice kind="success" title="Override recorded">The audit history and effective decision were refreshed.</Notice>}
    {result === "stale" && <Notice kind="error" title="Decision changed">
      Another reviewer updated this payout. Review the refreshed decision and reopen the override if a change is still needed.
    </Notice>}
    <div className="form-grid">
      <Card className="content-card">
        <div className="section-heading"><h2>Payout overview</h2><StatusBadge status={payout.team_status} /></div>
        <strong className="risk-amount">{formatCents(payout.payout_amount_cents)}</strong>
        <dl className="metadata-grid">
          <div><dt>Team</dt><dd>{payout.team_name} · {payout.team_id}</dd></div>
          <div><dt>Profession</dt><dd>{payout.profession}</dd></div>
          <div><dt>Captured payments</dt><dd>{formatNumber(payout.captured_count)}</dd></div>
          <div><dt>Created</dt><dd>{formatIST(payout.created_at)}</dd></div>
          <div className="metadata-wide"><dt>Coverage (IST)</dt><dd>{formatDate(payout.covers_from)} – {formatDate(payout.covers_to)}</dd></div>
        </dl>
      </Card>
      <Card className="content-card">
        <div className="section-heading"><h2>Effective decision</h2><StatusBadge status={effective.effective_decision} /></div>
        <p className="text-sm text-muted-foreground">{labelOf(effective.decision_source)} · Version {effective.effective_version}</p>
        <p className="break-words text-sm">{effective.reason}</p>
        <Evidence records={effective.evidence} />
        {effective.cluster_id && <Link className="text-link break-all text-sm"
          to={"/fraud" + contextSearch(params) + "#" + effective.cluster_id}>Fraud cluster {effective.cluster_id}</Link>}
        {canOverride && <Button className="w-fit" onClick={() => { setResult(null); setOverrideOpen(true) }}>
          <Plus className="size-4" aria-hidden="true" /> Override decision</Button>}
      </Card>
    </div>
    <Card className="content-card"><div className="section-heading"><h2>Original automated decision</h2>
      <StatusBadge status={effective.original_decision} /></div>
      {original && <><p className="text-sm">{original.reason}</p><Evidence records={original.evidence} /></>}
    </Card>
    <nav className="section-links" aria-label="Investigation sections">
      {[["transactions", "Transactions"], ["statistics", "Statistics"], ["fraud-evidence", "Fraud"], ["ml-evidence", "Model"], ["audit", "Audit history"]]
        .map(([id, label]) => <a key={id} href={`#${id}`}>{label}</a>)}
    </nav>
    <Card className="content-card" id="transactions">
      <div className="section-heading"><h2>Covered transactions</h2><span className="section-count">{transactions.length} attempts</span></div>
      {transactions.length ? <div className="table-scroll" role="region" aria-label="Covered transactions" tabIndex={0}>
        <table className="risk-table attempt-table">
          <thead><tr>{["Transaction", "Timestamp", "Amount", "Tip", "Method", "Status"].map((name) => <th scope="col" key={name}>{name}</th>)}</tr></thead>
          <tbody>{transactions.map((attempt) => <tr key={attempt.txn_id}>
            <th scope="row"><code>{attempt.txn_id}</code></th><td>{formatIST(attempt.created_at)}</td>
            <td>{formatCents(attempt.amount_cents)}</td><td>{formatCents(attempt.tip_cents)}</td>
            <td>{labelOf(attempt.payment_method)}</td><td><StatusBadge status={attempt.status} /></td>
          </tr>)}</tbody>
        </table>
      </div> : <p className="text-sm text-muted-foreground">No transaction attempts were published for this payout.</p>}
    </Card>
    <StatisticsPanel comparisons={statistics} />
    <FraudPanel fraud={fraud} />
    <MLPanel ml={ml} />
    {history.isError && <RetryNotice message={errorMessage(history.error)} onRetry={() => void history.refetch()} />}
    <AuditTimeline audit={audit} />
    {canOverride && overrideOpen && <OverrideDialog detail={detail} effective={effective}
      onClose={() => setOverrideOpen(false)}
      onResult={(status) => { setResult(status); setOverrideOpen(false) }} />}
  </>
}

export function PayoutDetailPage() {
  const { payoutId } = useParams()
  const [params] = useSearchParams()
  return <>
    <Link className="text-link inline-flex w-fit items-center gap-2 text-sm" to={`/payouts?${params}`}>
      <ArrowLeft className="size-4" aria-hidden="true" /> Back to payout queue
    </Link>
    <PageHeader title={`Payout ${payoutId ?? ""}`} description="Review the published decision, supporting evidence and scoped audit history." />
    <CompletedRun>{(run) => payoutId && <InvestigationView key={`${run.run_id}:${payoutId}`} payoutId={payoutId} runId={run.run_id} />}</CompletedRun>
  </>
}
