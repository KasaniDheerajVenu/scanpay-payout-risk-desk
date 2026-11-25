import { Download, ListChecks } from "lucide-react"
import { useMutation, useQuery } from "@tanstack/react-query"
import type { ReactNode } from "react"
import { Link, useSearchParams } from "react-router"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { EmptyPanel, LoadingPanel, Notice, RetryNotice } from "@/components/ui/workspace"
import { useWorkspace } from "@/features/auth/session"
import { contextSearch, useRunSelection } from "@/features/evaluations/selection"
import { errorMessage } from "@/services/api"
import { exportDecisions, type EvidenceRef, type MLStatus } from "@/services/risk"
import type { Evaluation } from "@/services/workspace"

export const riskQueryKey = (prefix: readonly string[], runId: string) => [...prefix, "risk", runId] as const

export function useRiskQuery<T>(runId: string, key: readonly unknown[], loader: (signal: AbortSignal) => Promise<T>) {
  const { prefix } = useWorkspace()
  return useQuery({ queryKey: [...riskQueryKey(prefix, runId), ...key],
    queryFn: ({ signal }) => loader(signal), retry: false })
}

export function CompletedRun({ children }: { children: (run: Evaluation) => ReactNode }) {
  const [params] = useSearchParams()
  const { selected, selectedId } = useRunSelection()
  if (selectedId && selected.isPending) return <LoadingPanel label="Loading selected run" />
  if (selectedId && selected.isError) return <RetryNotice message={errorMessage(selected.error)}
    onRetry={() => void selected.refetch()} />
  if (selected.data?.status === "COMPLETED") return children(selected.data)
  return <Card className="content-card">
    <EmptyPanel icon={ListChecks} title="Select a completed evaluation">
      Choose a published run to view its payouts and findings.
    </EmptyPanel>
    <Link className="text-link w-fit" to={"/evaluations" + contextSearch(params)}>Go to evaluations</Link>
  </Card>
}

export const evidenceKey = ({ type, id }: EvidenceRef) => `${type}:${id}`

export function Evidence({ records }: { records: EvidenceRef[] }) {
  if (!records.length) return <p className="text-sm text-muted-foreground">No supporting records.</p>
  return <ul className="evidence-list">
    {[...new Map(records.map((ref) => [evidenceKey(ref), ref])).values()]
      .map((ref) => <li key={evidenceKey(ref)}><span>{ref.type}</span><code>{ref.id}</code></li>)}
  </ul>
}

export const mlContext: Record<MLStatus, string> = {
  ACTIVE: "The model passed its quality gates and can contribute to review when the payout has supporting evidence.",
  DIAGNOSTIC_ONLY: "Scores are available for investigation. The model did not qualify to influence decisions.",
  INSUFFICIENT_HISTORY: "There were too few eligible historical observations or features to fit a model.",
  VALIDATION_FAILED: "Model fitting or validation failed. Decisions use the other available evidence.",
  INFERENCE_FAILED: "The model could not score this payout. Decisions use the other available evidence.",
}

export function ExportButton({ runId }: { runId: string }) {
  const download = useMutation({ mutationFn: async () => {
    const blob = await exportDecisions(runId)
    const url = URL.createObjectURL(blob)
    const link = document.createElement("a")
    link.href = url
    link.download = "decisions.csv"
    document.body.append(link)
    try { link.click() }
    finally { link.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 1000) }
  } })
  return <div className="grid justify-items-start gap-2">
    <Button variant="secondary" disabled={download.isPending} onClick={() => download.mutate()}>
      <Download className="size-4" aria-hidden="true" /> {download.isPending ? "Preparing CSV…" : "Export decisions"}
    </Button>
    {download.isSuccess && <span role="status" className="text-xs text-success">CSV download started.</span>}
    {download.isError && <Notice kind="error">{errorMessage(download.error)}</Notice>}
  </div>
}
