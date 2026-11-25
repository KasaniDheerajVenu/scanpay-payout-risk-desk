import { FlaskConical } from "lucide-react"
import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { EmptyPanel, LoadingPanel, PageHeader, RetryNotice, StatusBadge } from "@/components/ui/workspace"
import { CompletedRun, mlContext, useRiskQuery } from "@/features/risk/shared"
import { formatCents, formatIST, formatNumber, labelOf } from "@/lib/format"
import { errorMessage } from "@/services/api"
import { getModels, type Model, type ModelMetadata } from "@/services/risk"
import type { Evaluation } from "@/services/workspace"

const number = new Intl.NumberFormat("en-US", { maximumFractionDigits: 6 })
const metrics = [
  ["training_candidates", "Eligible training candidates"],
  ["training_fitted", "Training observations fitted"],
  ["calibration_scored", "Calibration observations scored"],
  ["validation_scored", "Validation observations scored"],
  ["validation_anomaly_count", "Validation anomalies"],
  ["validation_anomaly_rate", "Validation anomaly rate"],
  ["normal_history_alerts", "Alerts on apparently ordinary history"],
  ["verified_outlier_alerts", "Verified legitimate outlier alerts"],
  ["statistical_review_rate", "Statistical-only review rate"],
  ["assisted_review_rate", "ML-assisted review rate"],
  ["incremental_review_rate", "Incremental review rate"],
  ["flagged_exposure_cents", "Flagged historical exposure"],
] as const

function ModelDetails({ metadata, version }: { metadata: ModelMetadata; version?: Model }) {
  return <div className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-4">
    <div className="section-heading"><h3 className="font-semibold">Isolation Forest</h3><StatusBadge status={metadata.status} /></div>
    <p className="text-sm text-muted-foreground">{mlContext[metadata.status]}</p>
    <dl className="metadata-grid">
      {version && <>
        <div className="metadata-wide"><dt>Model version</dt><dd><code>{version.model_version_id}</code></dd></div>
        <div><dt>Fit cutoff</dt><dd>{formatIST(version.fit_cutoff_at)}</dd></div>
        <div><dt>Validation cutoff</dt><dd>{formatIST(version.validation_cutoff_at)}</dd></div>
        <div><dt>Calibration cutoff</dt><dd>{formatIST(version.calibration_cutoff_at)}</dd></div>
        <div><dt>Published</dt><dd>{formatIST(version.created_at)}</dd></div>
        <div><dt>Training observations</dt><dd>{formatNumber(version.training_observation_count)}</dd></div>
        <div><dt>Validation observations</dt><dd>{formatNumber(version.validation_observation_count)}</dd></div>
      </>}
      <div><dt>Feature schema</dt><dd><code>{metadata.feature_schema_version}</code></dd></div>
      <div><dt>Anomaly threshold</dt><dd>{metadata.anomaly_threshold == null ? "Unavailable" : number.format(metadata.anomaly_threshold)}</dd></div>
    </dl>
    <div><h4 className="mb-2 text-sm font-semibold">Historical validation metrics</h4>
      <dl className="metadata-grid">{metrics.flatMap(([key, label]) => {
        const value = metadata.metrics[key]
        return typeof value === "number" ? [<div key={key}><dt>{label}</dt><dd>{key.endsWith("_cents")
          ? formatCents(value) : key.endsWith("_rate") ? `${number.format(value * 100)}%` : formatNumber(value)}</dd></div>] : []
      })}</dl>
    </div>
    <div><h4 className="mb-2 text-sm font-semibold">Quality gates</h4>
      {!metadata.quality_gates.length ? <p className="text-sm text-muted-foreground">No gate results were published for this model.</p>
        : <div className="table-scroll" role="region" aria-label="Model quality gates" tabIndex={0}>
          <table className="risk-table"><thead><tr><th scope="col">Gate</th><th scope="col">Observed</th>
            <th scope="col">Limit</th><th scope="col">Outcome</th></tr></thead>
            <tbody>{metadata.quality_gates.map((gate) => <tr key={gate.code}>
              <th scope="row">{labelOf(gate.code)}</th><td>{gate.observed == null ? "Unavailable" : number.format(gate.observed)}</td>
              <td>{number.format(gate.limit)}</td><td><StatusBadge status={gate.passed ? "PASSED" : "FAILED"} /></td>
            </tr>)}</tbody>
          </table>
        </div>}
    </div>
  </div>
}

function ModelResults({ run }: { run: Evaluation }) {
  const [offset, setOffset] = useState(0)
  const models = useRiskQuery(run.run_id, ["models", offset], (signal) => getModels(offset, signal))
  const selected = run.model ?? run.summary?.ml.metadata
  return <>
    <Card className="content-card">
      <div className="section-heading"><h2>Selected-run model</h2></div>
      {selected ? <ModelDetails metadata={selected} version={run.model ?? undefined} />
        : <EmptyPanel icon={FlaskConical} title="Model metadata unavailable">
          No model metadata was published for the selected evaluation.
        </EmptyPanel>}
    </Card>
    {models.isPending ? <LoadingPanel label="Loading model versions" /> : models.isError
      ? <RetryNotice message={errorMessage(models.error)} onRetry={() => void models.refetch()} />
      : <Card className="content-card">
        <div className="section-heading"><h2>Published model versions</h2></div>
        {models.data.length ? models.data.map((model) => <details key={model.model_version_id} className="risk-disclosure">
          <summary><span className="break-all">{model.model_version_id}</span><StatusBadge status={model.status} /></summary>
          <div className="mt-4"><ModelDetails metadata={model} version={model} /></div>
        </details>) : <EmptyPanel icon={FlaskConical} title="No versions on this page">Model versions appear after successful fitting.</EmptyPanel>}
        {(offset > 0 || models.data.length === 20) && <div className="flex flex-wrap gap-2">
          <Button variant="secondary" disabled={!offset} onClick={() => setOffset(offset - 20)}>Newer versions</Button>
          <Button variant="secondary" disabled={models.data.length < 20} onClick={() => setOffset(offset + 20)}>Older versions</Button>
        </div>}
      </Card>}
  </>
}

export function ModelsPage() {
  return <><PageHeader title="Model insights" description="Inspect Isolation Forest readiness, validation and model versions." />
    <CompletedRun>{(run) => <ModelResults key={run.run_id} run={run} />}</CompletedRun></>
}
