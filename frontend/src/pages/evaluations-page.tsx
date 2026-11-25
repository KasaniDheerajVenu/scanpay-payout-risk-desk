import { Activity, ArrowRight, Check, ChevronLeft, ChevronRight, Play, RefreshCw } from "lucide-react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useRef, useState, type FormEvent } from "react"
import { useSearchParams } from "react-router"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { EmptyPanel, LoadingPanel, Notice, PageHeader, RetryNotice, StatusBadge } from "@/components/ui/workspace"
import { hasPermission, useWorkspace } from "@/features/auth/session"
import { useRunSelection } from "@/features/evaluations/selection"
import { formatCents, formatIST, formatNumber, labelOf } from "@/lib/format"
import { errorMessage, issueMessage } from "@/services/api"
import { batchKey, getBatches, getRun, getRuns, runKey, startRun, type Evaluation } from "@/services/workspace"

const inProgress = (run?: Evaluation) => run?.status === "RUNNING" || run?.status === "QUEUED"

function RunInspector({ run, isSelected, onSelect, onRetry }: {
  run: Evaluation; isSelected: boolean; onSelect: () => void; onRetry?: () => void
}) {
  const modelStatus = run.model?.status ?? run.summary?.ml.status
  return (
    <Card className="content-card">
      <div className="section-heading"><h2>Run details</h2><StatusBadge status={run.status} /></div>
      <dl className="metadata-grid">
        <div className="metadata-wide"><dt>Run ID</dt><dd><code>{run.run_id}</code></dd></div>
        <div className="metadata-wide"><dt>Batch ID</dt><dd><code>{run.batch_id}</code></dd></div>
        <div><dt>Cutoff</dt><dd>{formatIST(run.cutoff_at)}</dd></div>
        <div><dt>Created</dt><dd>{formatIST(run.created_at)}</dd></div>
        <div><dt>Started</dt><dd>{formatIST(run.started_at)}</dd></div>
        <div><dt>Completed</dt><dd>{formatIST(run.completed_at)}</dd></div>
        <div><dt>Rule version</dt><dd>{run.rule_version}</dd></div>
        <div><dt>Model status</dt><dd>{modelStatus ? <StatusBadge status={modelStatus} /> : "Unavailable"}</dd></div>
      </dl>
      {run.summary && <>
        <dl className="stats-grid">
          <div><dt>Payouts</dt><dd>{formatNumber(run.summary.payout_count)}</dd></div>
          <div><dt>Total payout amount</dt><dd>{formatCents(run.summary.total_cents)}</dd></div>
        </dl>
        <div className="decision-summary">
          <h3>Automated results</h3>
          {(["APPROVE", "MANUAL_REVIEW", "REJECT"] as const).map((decision) => (
            <div key={decision}><StatusBadge status={decision} />
              <span>{formatNumber(run.summary!.decisions[decision].count)} · {formatCents(run.summary!.decisions[decision].cents)}</span></div>
          ))}
        </div>
      </>}
      {run.status === "FAILED" && <Notice kind="error" title="Evaluation failed">
        {issueMessage(run.failure) ?? "This run did not publish results."}
        {onRetry && <div className="mt-2"><Button variant="secondary" onClick={onRetry}>Select dataset to retry</Button></div>}
      </Notice>}
      {run.status === "COMPLETED" && <Button onClick={onSelect} disabled={isSelected} className="w-fit">
        {isSelected ? <><Check className="size-4" aria-hidden="true" /> Selected run</> : "Use this run"}
      </Button>}
    </Card>
  )
}

export function EvaluationsPage() {
  const { user, prefix } = useWorkspace()
  const canEvaluate = hasPermission(user, "run_evaluations")
  const client = useQueryClient()
  const [params, setParams] = useSearchParams()
  const batchId = params.get("batch_id") ?? ""
  const { selectedId, setSelected } = useRunSelection()
  const inspectId = params.get("inspect_id") ?? selectedId
  const [offset, setOffset] = useState(0)
  const lock = useRef(false)
  const launch = useMutation({ mutationFn: startRun })
  const batches = useQuery({ queryKey: [...prefix, ...batchKey, "list"],
    queryFn: ({ signal }) => getBatches(signal), enabled: canEvaluate, retry: false })
  const runs = useQuery({ queryKey: [...prefix, ...runKey, "list", offset],
    queryFn: ({ signal }) => getRuns(signal, offset), retry: false,
    refetchInterval: (query) => query.state.data?.some(inProgress) ? 5000 : false })
  const inspected = useQuery({ queryKey: [...prefix, ...runKey, "detail", inspectId],
    queryFn: ({ signal }) => getRun(inspectId!, signal), enabled: !!inspectId, retry: false,
    refetchInterval: (query) => inProgress(query.state.data) ? 5000 : false })
  const batch = batches.data?.find((entry) => entry.batch_id === batchId)
  const ready = batch?.status === "READY"

  function setParam(key: "batch_id" | "inspect_id", value: string) {
    setParams((previous) => {
      const next = new URLSearchParams(previous)
      if (value) next.set(key, value)
      else next.delete(key)
      return next
    })
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (lock.current || !ready) return
    lock.current = true
    launch.mutate(batchId, {
      onSuccess: ({ data }) => {
        client.setQueryData([...prefix, ...runKey, "detail", data.run_id], data)
        void client.invalidateQueries({ queryKey: [...prefix, ...runKey, "list"] })
      },
      onError: () => { void client.invalidateQueries({ queryKey: [...prefix, ...runKey, "list"] }) },
      onSettled: () => { lock.current = false },
    })
  }

  return (
    <>
      <PageHeader title="Evaluations" description="Run payout evaluations and review published results."
        action={<Button variant="secondary" onClick={() => void runs.refetch()} disabled={runs.isFetching}>
          <RefreshCw aria-hidden="true" className="size-4" /> Refresh history
        </Button>} />
      {canEvaluate && <Card className="content-card">
        <div className="section-heading"><h2>Run evaluation</h2></div>
        <form className="grid gap-4" onSubmit={submit}>
          <div className="evaluation-form">
            <label className="field min-w-0"><span>Dataset batch</span>
              <select value={batchId} disabled={launch.isPending || batches.isPending}
                onChange={(event) => setParam("batch_id", event.target.value)}>
                <option value="">Select a ready dataset</option>
                {batchId && !batch && <option value={batchId} disabled>Selected batch unavailable</option>}
                {batches.data?.map((entry) => <option key={entry.batch_id} value={entry.batch_id} disabled={entry.status !== "READY"}>
                  {labelOf(entry.mode)} · {formatIST(entry.imported_at)} · {entry.batch_id} · {labelOf(entry.status)}
                </option>)}
              </select>
            </label>
            <Button type="submit" disabled={!ready || launch.isPending}>
              <Play className="size-4" aria-hidden="true" />
              {launch.isPending ? "Evaluating…" : launch.isError ? "Retry evaluation" : "Start evaluation"}
            </Button>
          </div>
          {batch && <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
            <StatusBadge status={batch.status} /> Cutoff {formatIST(batch.cutoff)}
          </div>}
          <p className="text-sm text-muted-foreground">New runs do not inherit analyst overrides from earlier runs.</p>
          {batches.isError && <RetryNotice message={errorMessage(batches.error)} onRetry={() => void batches.refetch()} />}
          {launch.isError && <Notice kind="error">{errorMessage(launch.error, "evaluation")}</Notice>}
          {launch.isPending && <p role="status" className="text-sm text-muted-foreground">Waiting for the evaluation result.</p>}
        </form>
        {launch.isSuccess && <Notice kind="success" title={launch.data.status === 200 ? "Existing run reused" : "Evaluation completed"}>
          <span className="break-all">{launch.data.data.run_id}</span>
          <button type="button" className="text-link ml-2 inline-flex items-center gap-1"
            onClick={() => setParam("inspect_id", launch.data.data.run_id)}>
            Inspect completed run <ArrowRight className="size-3.5" aria-hidden="true" />
          </button>
        </Notice>}
      </Card>}
      <div className="detail-layout">
        <div className="min-w-0">
          {runs.isPending ? <LoadingPanel label="Loading evaluation history" /> : runs.isError
            ? <RetryNotice message={errorMessage(runs.error)} onRetry={() => void runs.refetch()} />
            : <Card className="content-card">
              <div className="section-heading"><h2>Evaluation history</h2></div>
              {runs.data.length === 0 ? <EmptyPanel icon={Activity} title="No runs on this page">
                Completed and in-progress evaluations appear here.
              </EmptyPanel> : <ul className="record-list">
                {runs.data.map((run) => <li key={run.run_id}>
                  <button type="button" className="record-row" onClick={() => setParam("inspect_id", run.run_id)}
                    aria-label={`Inspect run ${run.run_id}`} aria-pressed={run.run_id === inspectId}>
                    <span className="record-icon"><Activity className="size-4" aria-hidden="true" /></span>
                    <span className="record-copy"><strong>{run.run_id}</strong><small>{formatIST(run.created_at)}</small></span>
                    <StatusBadge status={run.status} />
                  </button>
                </li>)}
              </ul>}
              {(offset > 0 || runs.data.length === 20) && <div className="flex items-center justify-between gap-2">
                <Button variant="secondary" disabled={offset === 0} onClick={() => setOffset(offset - 20)}>
                  <ChevronLeft className="size-4" aria-hidden="true" /> Previous
                </Button>
                <Button variant="secondary" disabled={runs.data.length < 20} onClick={() => setOffset(offset + 20)}>
                  Next <ChevronRight className="size-4" aria-hidden="true" />
                </Button>
              </div>}
            </Card>}
        </div>
        <div className="min-w-0">
          {!inspectId ? <Card className="content-card"><EmptyPanel icon={Activity} title="Inspect a run">
            Select a run from evaluation history to review its status and results.
          </EmptyPanel></Card>
            : inspected.isPending ? <LoadingPanel label="Loading run details" />
              : inspected.isError ? <RetryNotice message={errorMessage(inspected.error)} onRetry={() => void inspected.refetch()} />
                : <RunInspector run={inspected.data} isSelected={inspected.data.run_id === selectedId}
                  onSelect={() => setSelected(inspected.data)}
                  onRetry={canEvaluate ? () => setParam("batch_id", inspected.data.batch_id) : undefined} />}
        </div>
      </div>
    </>
  )
}
