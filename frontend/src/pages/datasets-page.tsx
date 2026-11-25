import { ArrowRight, Database, FileSpreadsheet, Plus, X } from "lucide-react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { Link, useSearchParams } from "react-router"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { EmptyPanel, LoadingPanel, Notice, PageHeader, RetryNotice, StatusBadge } from "@/components/ui/workspace"
import { hasPermission, useWorkspace } from "@/features/auth/session"
import { DemoImportPanel, UploadPanel, ValidationLocation } from "@/features/datasets/import-panel"
import { fileLabels } from "@/features/datasets/files"
import { contextSearch } from "@/features/evaluations/selection"
import { formatBytes, formatIST, formatNumber, labelOf } from "@/lib/format"
import { errorMessage, issueMessage } from "@/services/api"
import { batchKey, getBatch, getBatches, type BatchDetail } from "@/services/workspace"

function BatchInspector({ batch, runHref }: { batch: BatchDetail; runHref?: string }) {
  const issue = batch.validation
  return (
    <Card className="content-card">
      <div className="section-heading"><h2>Batch details</h2><StatusBadge status={batch.status} /></div>
      <dl className="metadata-grid">
        <div className="metadata-wide"><dt>Batch ID</dt><dd><code>{batch.batch_id}</code></dd></div>
        <div><dt>Mode</dt><dd>{labelOf(batch.mode)}</dd></div>
        <div><dt>Imported</dt><dd>{formatIST(batch.imported_at)}</dd></div>
        <div className="metadata-wide"><dt>Evaluation cutoff</dt><dd>{formatIST(batch.cutoff)}</dd></div>
      </dl>
      <dl className="stats-grid">
        {([
          ["Records", batch.counts.rows], ["Inserted", batch.counts.inserted],
          ["Unchanged", batch.counts.unchanged], ["Dispositions completed", batch.counts.dispositions_completed],
        ] as const).map(([label, count]) => <div key={label}><dt>{label}</dt><dd>{formatNumber(count)}</dd></div>)}
      </dl>
      {issue && <Notice kind="error" title="Validation failed">
        {issueMessage(issue.code) ?? "The dataset did not pass validation. Review the referenced file."}
        <ValidationLocation file={issue.file_type} row={issue.row} field={issue.field} />
      </Notice>}
      {runHref && <Link to={runHref} className="text-link inline-flex items-center gap-2">
        Use dataset for evaluation <ArrowRight className="size-4" aria-hidden="true" />
      </Link>}
      <div className="file-results">
        <h3>Source files</h3>
        {batch.files.length === 0 && <p className="text-sm text-muted-foreground">No file metadata is available.</p>}
        {batch.files.map((file) => <details key={file.file_type}>
          <summary>
            <FileSpreadsheet className="size-4 shrink-0 text-primary" aria-hidden="true" />
            <span className="min-w-0 flex-1"><strong>{fileLabels[file.file_type]}</strong>
              <small className="block break-all">{file.filename}</small></span>
            <small>{formatBytes(file.bytes_count)}</small>
          </summary>
          <dl className="metadata-grid pt-3">
            <div><dt>Rows</dt><dd>{formatNumber(file.row_count)}</dd></div>
            <div><dt>Inserted / unchanged</dt><dd>{formatNumber(file.inserted_count)} / {formatNumber(file.unchanged_count)}</dd></div>
            <div><dt>Dispositions completed</dt><dd>{formatNumber(file.validation_summary.dispositions_completed)}</dd></div>
            <div><dt>Incomplete historical windows</dt><dd>{formatNumber(file.validation_summary.incomplete_historical_windows)}</dd></div>
            <div className="metadata-wide"><dt>SHA-256</dt><dd><code>{file.sha256}</code></dd></div>
          </dl>
        </details>)}
      </div>
    </Card>
  )
}

export function DatasetsPage() {
  const { user, prefix } = useWorkspace()
  const client = useQueryClient()
  const [params, setParams] = useSearchParams()
  const batchId = params.get("batch_id")
  const [importOpen, setImportOpen] = useState(false)
  const [imported, setImported] = useState<{ batch: BatchDetail; reused: boolean }>()
  const canUpload = user.mode === "local" && hasPermission(user, "upload_datasets")
  const canDemoImport = user.mode === "demo" && hasPermission(user, "import_synthetic_demo")
  const list = useQuery({ queryKey: [...prefix, ...batchKey, "list"], queryFn: ({ signal }) => getBatches(signal), retry: false })
  const detail = useQuery({ queryKey: [...prefix, ...batchKey, "detail", batchId],
    queryFn: ({ signal }) => getBatch(batchId!, signal), enabled: !!batchId, retry: false })

  function select(id: string) {
    setParams((previous) => {
      const next = new URLSearchParams(previous)
      next.set("batch_id", id)
      return next
    })
  }

  function onImported(batch: BatchDetail, reused: boolean) {
    client.setQueryData([...prefix, ...batchKey, "detail", batch.batch_id], batch)
    refreshHistory()
    setImported({ batch, reused })
    setImportOpen(false)
    select(batch.batch_id)
  }

  function refreshHistory() {
    void client.invalidateQueries({ queryKey: [...prefix, ...batchKey, "list"] })
  }

  return (
    <>
      <PageHeader title="Datasets" description="Manage dataset imports and inspect batch readiness."
        action={(canUpload || canDemoImport) && <Button onClick={() => setImportOpen(!importOpen)} aria-expanded={importOpen}>
          {importOpen ? <X className="size-4" aria-hidden="true" /> : <Plus className="size-4" aria-hidden="true" />}
          {importOpen ? "Close import form" : canDemoImport ? "Import synthetic preset" : "Import dataset"}
        </Button>} />
      {importOpen && canUpload && <UploadPanel onImported={onImported} onFailed={refreshHistory} />}
      {importOpen && canDemoImport && <DemoImportPanel onImported={onImported} onFailed={refreshHistory} />}
      {imported && <Notice kind="success" title={imported.reused ? "Existing batch reused" : "Dataset imported"}>
        {formatNumber(imported.batch.counts.rows)} records · {labelOf(imported.batch.status)} · {imported.batch.batch_id}
      </Notice>}
      <div className="detail-layout">
        <div className="min-w-0">
          {list.isPending ? <LoadingPanel label="Loading datasets" /> : list.isError
            ? <RetryNotice message={errorMessage(list.error)} onRetry={() => void list.refetch()} />
            : <Card className="content-card">
              <div className="section-heading"><h2>Dataset history</h2>
                <span className="section-count">{formatNumber(list.data.length)} {list.data.length === 1 ? "batch" : "batches"}</span>
              </div>
              {list.data.length === 0 ? <EmptyPanel icon={Database} title="No batches yet">
                {canUpload || canDemoImport ? "Import a dataset to begin an evaluation." : "Batches appear after an administrator imports them."}
              </EmptyPanel> : <ul className="record-list">
                {list.data.map((batch) => <li key={batch.batch_id}>
                  <button type="button" className="record-row" onClick={() => select(batch.batch_id)}
                    aria-label={`Inspect batch ${batch.batch_id}`} aria-pressed={batch.batch_id === batchId}>
                    <span className="record-icon"><Database aria-hidden="true" className="size-4" /></span>
                    <span className="record-copy"><strong>{batch.batch_id}</strong>
                      <small>{labelOf(batch.mode)} · {formatIST(batch.imported_at)} · {formatNumber(batch.counts.rows)} records</small>
                    </span>
                    <StatusBadge status={batch.status} />
                  </button>
                </li>)}
              </ul>}
            </Card>}
        </div>
        <div className="min-w-0">
          {!batchId ? <Card className="content-card"><EmptyPanel icon={FileSpreadsheet} title="Select a batch">
            Inspect its cutoff, counts and source file metadata.
          </EmptyPanel></Card>
            : detail.isPending ? <LoadingPanel label="Loading batch details" />
              : detail.isError ? <RetryNotice message={errorMessage(detail.error)} onRetry={() => void detail.refetch()} />
                : <BatchInspector batch={detail.data} runHref={detail.data.status === "READY" &&
                  hasPermission(user, "run_evaluations") ? "/evaluations" + contextSearch(params) : undefined} />}
        </div>
      </div>
    </>
  )
}
