import { ArrowRight, ArrowDown, ScanSearch } from "lucide-react"
import { Card } from "@/components/ui/card"
import { EmptyPanel, StatusBadge } from "@/components/ui/workspace"
import { formatCents, formatIST, formatNumber, labelOf } from "@/lib/format"
import type { Audit, Comparison, Finding, Fraud, ML, Path } from "@/services/risk"
import { Evidence, mlContext } from "./shared"

export const featureLabel = (value: string) => labelOf(value.replace(/_cents$/, ""))
const scalar = new Intl.NumberFormat("en-US", { maximumFractionDigits: 5 })
export function featureValue(value: string | number | null, unit: string) {
  if (value == null || !Number.isFinite(Number(value))) return "Unavailable"
  if (unit === "cents") return formatCents(Number(value))
  if (unit === "ratio") return `${scalar.format(Number(value) * 100)}%`
  return scalar.format(Number(value))
}

function QuartileRange({ finding }: { finding: Finding }) {
  const { q1, q3, median } = finding.baseline
  if ([q1, q3, median, finding.observed].some((value) => value == null)) return null
  const [a, b, m, observed] = [q1, q3, median, finding.observed].map(Number)
  const minimum = Math.min(a, observed, Number(finding.lower_fence ?? a))
  const maximum = Math.max(b, observed, Number(finding.upper_fence ?? b))
  const position = (value: number) => maximum === minimum ? 50 : ((value - minimum) / (maximum - minimum)) * 100
  return <div className="quartile-range" role="img" aria-label={`Observed ${featureValue(observed, finding.unit)}, first quartile ${featureValue(a, finding.unit)}, median ${featureValue(m, finding.unit)}, third quartile ${featureValue(b, finding.unit)}`}>
    <span className="quartile-band" style={{ left: `${position(a)}%`, width: `${position(b) - position(a)}%` }} />
    <span className="quartile-median" style={{ left: `${position(m)}%` }} />
    <span className="quartile-observed" style={{ left: `${position(observed)}%` }} />
  </div>
}

function BaselinePanel({ finding }: { finding: Finding }) {
  const { baseline, unit } = finding
  const fields = [
    ["Observed", featureValue(finding.observed, unit)], ["Sample count", formatNumber(baseline.sample_count)],
    ["First quartile", featureValue(baseline.q1, unit)], ["Median", featureValue(baseline.median, unit)],
    ["Third quartile", featureValue(baseline.q3, unit)], ["Interquartile range", featureValue(baseline.iqr, unit)],
    ["Lower fence", featureValue(finding.lower_fence, unit)], ["Upper fence", featureValue(finding.upper_fence, unit)],
    ["Materiality", finding.is_material == null ? "Unavailable" : finding.is_material ? "Material" : "Below materiality threshold"],
    ["Minimum absolute deviation", featureValue(finding.minimum_absolute, unit)],
    ["Minimum relative deviation", featureValue(finding.minimum_relative, "ratio")],
    ["IQR multiplier", finding.iqr_multiplier ?? "Unavailable"],
  ]
  return <div className="risk-nested">
    <div className="section-heading"><h3 className="font-semibold">{labelOf(baseline.scope)} baseline</h3><StatusBadge status={finding.status} /></div>
    <p className="mb-3 text-xs text-muted-foreground">
      {labelOf(baseline.reliability)}{baseline.peer_team_count != null && ` · ${baseline.peer_team_count} peer teams`}
      {finding.observation_availability !== "available" && ` · Observation ${labelOf(finding.observation_availability).toLowerCase()}`}
    </p>
    <QuartileRange finding={finding} />
    <dl className="metadata-grid">{fields.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
    <p className="mt-3 text-sm text-muted-foreground">{finding.explanation}</p>
  </div>
}

export function StatisticsPanel({ comparisons }: { comparisons: Comparison[] }) {
  return <Card className="content-card" id="statistics">
    <div className="section-heading"><h2>Statistical analysis</h2><span className="section-count">Team and profession baselines</span></div>
    {!comparisons.length ? <p className="text-sm text-muted-foreground">No published statistical findings.</p>
      : comparisons.map(({ team, profession, preferred_scope }, index) => (
        <details key={team.feature} className="risk-disclosure" open={index === 0}>
          <summary><span>{featureLabel(team.feature)}</span>
            <small>{preferred_scope ? `Preferred baseline · ${labelOf(preferred_scope)}` : "No preferred baseline"}</small>
          </summary>
          <div className="form-grid mt-3"><BaselinePanel finding={team} /><BaselinePanel finding={profession} /></div>
        </details>
      ))}
  </Card>
}

export function EvidencePath({ path }: { path: Path }) {
  return <details className="risk-disclosure">
    <summary>Evidence path from {path.seed_payout_id} <small>{path.steps.length} relationships</small></summary>
    <ol className="path-steps">
      {path.steps.map((step, index) => <li key={index}>
        <div className="path-edge">
          <code>{labelOf(step.start.kind)}{step.start.source_id && ` · ${step.start.source_id}`}</code>
          <ArrowRight aria-hidden="true" className="size-4 shrink-0 text-primary" />
          <code>{labelOf(step.end.kind)}{step.end.source_id && ` · ${step.end.source_id}`}</code>
        </div>
        <p className="text-xs text-muted-foreground">{labelOf(step.relationship)} · {labelOf(step.strength)}
          {step.supports_exposure && " · Supports exposure"}</p>
        <Evidence records={step.evidence} />
      </li>)}
    </ol>
  </details>
}

export function FraudPanel({ fraud }: { fraud: Fraud | null }) {
  return <Card className="content-card" id="fraud-evidence">
    <div className="section-heading"><h2>Fraud analysis</h2>{fraud && <StatusBadge status={fraud.status} />}</div>
    {!fraud ? <EmptyPanel icon={ScanSearch} title="Fraud findings unavailable">No fraud analysis was published for this payout.</EmptyPanel> : <>
      <p className="text-sm">{fraud.explanation}</p>
      {[fraud.primary, ...fraud.alternatives].flatMap((match) => match ? [match] : []).map((match) => <div className="risk-nested" key={match.stable_case_key}>
        <strong className="text-sm">{labelOf(match.relationship)} · {labelOf(match.strength)}</strong>
        <p className="break-all font-mono text-xs text-muted-foreground">{match.stable_case_key}</p>
        <div className="mt-3 grid gap-2">{match.paths.map((path, index) => <EvidencePath key={index} path={path} />)}</div>
      </div>)}
      {fraud.leads.length > 0 && <div className="grid gap-2"><h3 className="text-sm font-semibold">Investigative leads</h3>
        <p className="text-xs text-muted-foreground">These links require further investigation before they can support established exposure.</p>
        {fraud.leads.map((lead, index) => <div className="risk-nested" key={index}>
          <strong className="text-sm">{labelOf(lead.relationship)} · {labelOf(lead.strength)}</strong>
          <Evidence records={[...lead.current_evidence, ...lead.historical_evidence]} />
        </div>)}
      </div>}
    </>}
  </Card>
}

export function MLPanel({ ml }: { ml: ML | null }) {
  return <Card className="content-card" id="ml-evidence">
    <div className="section-heading"><h2>Isolation Forest analysis</h2>{ml && <StatusBadge status={ml.status} />}</div>
    {!ml ? <p className="text-sm text-muted-foreground">No ML finding was published for this payout.</p> : <>
      <p className="text-sm">{ml.explanation}</p>
      <p className="text-xs text-muted-foreground">{mlContext[ml.status]} Anomaly scores measure unusualness.</p>
      <dl className="metadata-grid">
        <div><dt>Anomaly score</dt><dd>{ml.anomaly_score == null ? "Unavailable" : scalar.format(ml.anomaly_score)}</dd></div>
        <div><dt>Threshold</dt><dd>{ml.anomaly_threshold == null ? "Unavailable" : scalar.format(ml.anomaly_threshold)}</dd></div>
        <div><dt>Anomaly flag</dt><dd>{ml.is_anomaly == null ? "Unavailable" : ml.is_anomaly ? "Flagged" : "Clear"}</dd></div>
        <div><dt>Eligible to influence review</dt><dd>{ml.qualifies_for_review ? "Yes" : "No"}</dd></div>
        <div><dt>Observed / retained features</dt><dd>{ml.observed_model_features} / {ml.retained_model_features}</dd></div>
        {ml.diagnostic_code && <div><dt>Diagnostic</dt><dd>{labelOf(ml.diagnostic_code)}</dd></div>}
      </dl>
      {ml.unusual_features.map((feature, index) => <div className="risk-nested" key={index}>
        <strong className="text-sm">{featureLabel(feature.feature)}</strong>
        <p className="text-sm">{feature.comparison}</p>
        <Evidence records={feature.evidence} />
      </div>)}
      {ml.feature_availability.length > 0 && <details className="risk-disclosure"><summary>Model feature availability</summary>
        <dl className="metadata-grid mt-3">{ml.feature_availability.map(({ feature, availability }) => <div key={feature}>
          <dt>{featureLabel(feature)}</dt><dd>{labelOf(availability)}</dd>
        </div>)}</dl>
      </details>}
    </>}
  </Card>
}

export function AuditTimeline({ audit }: { audit: Audit }) {
  return <Card className="content-card" id="audit">
    <div className="section-heading"><h2>Audit history</h2><span className="section-count">Version {audit.effective.effective_version} is current</span></div>
    <ol className="audit-timeline">{[...audit.history].reverse().map((event) => <li key={event.version}>
      <div className="section-heading">
        <strong className="text-sm">Version {event.version} · {labelOf(event.decision_source)}</strong>
        <StatusBadge status={event.status} />
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {event.previous_decision && <><StatusBadge status={event.previous_decision} /><ArrowRight aria-hidden="true" className="size-4" /></>}
        <StatusBadge status={event.new_decision} />
      </div>
      <p className="text-xs text-muted-foreground">{event.actor?.display_name ?? "Automated evaluation"} · {formatIST(event.timestamp)}</p>
      <p className="break-words text-sm">{event.reason}</p>
      <Evidence records={event.evidence} />
      {event.status === "CURRENT" && event.version > 1 && <ArrowDown aria-hidden="true" className="size-4 text-muted-foreground" />}
    </li>)}</ol>
  </Card>
}
