import { FolderSearch } from "lucide-react"
import { useEffect } from "react"
import { Link, useLocation, useSearchParams } from "react-router"
import { Card } from "@/components/ui/card"
import { EmptyPanel, LoadingPanel, PageHeader, RetryNotice } from "@/components/ui/workspace"
import { contextSearch } from "@/features/evaluations/selection"
import { EvidencePath } from "@/features/risk/evidence-panels"
import { CompletedRun, Evidence, useRiskQuery } from "@/features/risk/shared"
import { labelOf } from "@/lib/format"
import { errorMessage } from "@/services/api"
import { getClusters } from "@/services/risk"

function FraudResults({ runId }: { runId: string }) {
  const [params] = useSearchParams()
  const { hash } = useLocation()
  const clusters = useRiskQuery(runId, ["clusters"], (signal) => getClusters(runId, signal))
  useEffect(() => {
    if (hash && clusters.isSuccess) document.getElementById(hash.slice(1))?.scrollIntoView?.({ block: "start" })
  }, [hash, clusters.isSuccess])
  if (clusters.isPending) return <LoadingPanel label="Loading fraud clusters" />
  if (clusters.isError) return <RetryNotice message={errorMessage(clusters.error)} onRetry={() => void clusters.refetch()} />
  if (!clusters.data.length) return <Card className="content-card"><EmptyPanel icon={FolderSearch} title="No known clusters">
    No established historical fraud clusters were published for this run.
  </EmptyPanel></Card>
  const context = contextSearch(params)
  return <>{clusters.data.map((cluster) => <Card className="content-card fraud-card" key={cluster.cluster_id} id={cluster.cluster_id}>
    <div className="section-heading"><h2>{labelOf(cluster.reason_code)}</h2><span className="section-count">{cluster.associated_payouts.length} associated payouts</span></div>
    <p className="text-sm">{cluster.summary}</p>
    <dl className="metadata-grid">
      <div><dt>Run cluster ID</dt><dd><code>{cluster.cluster_id}</code></dd></div>
      <div><dt>Historical seed payout</dt><dd>{cluster.seed_payout_id}</dd></div>
      <div className="metadata-wide"><dt>Stable case ID</dt><dd><code>{cluster.stable_case_key}</code></dd></div>
      <div className="metadata-wide"><dt>Historical categories</dt><dd>{cluster.reason_codes.map(labelOf).join(", ")}</dd></div>
    </dl>
    <Evidence records={cluster.evidence} />
    <h3 className="text-sm font-semibold">Associated current payouts</h3>
    {!cluster.associated_payouts.length && <p className="text-sm text-muted-foreground">No current payouts are associated with this cluster.</p>}
    {cluster.associated_payouts.map((payout) => <div className="risk-nested grid gap-2" key={payout.payout_id}>
      <div className="section-heading">
        <Link className="text-link" to={`/payouts/${encodeURIComponent(payout.payout_id)}${context}`}>Payout {payout.payout_id}</Link>
        <span className="text-xs text-muted-foreground">{payout.effective_cluster ? "Effective cluster" : payout.original_cluster ? "Original automated cluster" : "Published exposure path"}</span>
      </div>
      {payout.paths.map((path, index) => <EvidencePath key={index} path={path} />)}
    </div>)}
  </Card>)}</>
}

export function FraudPage() {
  return <><PageHeader title="Fraud insights" description="Inspect known historical clusters and validated links to payouts in this run." />
    <CompletedRun>{(run) => <FraudResults key={run.run_id} runId={run.run_id} />}</CompletedRun></>
}
