import { X } from "lucide-react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useEffect, useRef, useState, type FormEvent } from "react"
import { Button } from "@/components/ui/button"
import { Notice, RetryNotice, StatusBadge } from "@/components/ui/workspace"
import { useWorkspace } from "@/features/auth/session"
import { labelOf } from "@/lib/format"
import { ApiError, errorMessage } from "@/services/api"
import { getClusters, overridePayout, type Decision, type EffectiveDecision, type EvidenceRef, type Investigation, type Path } from "@/services/risk"
import { Evidence, evidenceKey, riskQueryKey, useRiskQuery } from "./shared"

function publishedRecords(detail: Investigation, effective: EffectiveDecision) {
  const { payout, fraud, ml } = detail
  const refs: EvidenceRef[] = [
    { type: "payout", id: payout.payout_id }, { type: "team", id: payout.team_id },
    ...effective.evidence, ...(detail.decisions.history.find((event) => event.version === 1)?.evidence ?? []),
    ...detail.transactions.map((attempt) => ({ type: "transaction" as const, id: attempt.txn_id })),
    ...(ml?.evidence ?? []), ...(fraud?.current_evidence ?? []),
  ]
  for (const { team, profession } of detail.statistics) {
    for (const { baseline } of [team, profession]) {
      refs.push(...baseline.payout_ids.map((id) => ({ type: "payout" as const, id })),
        ...baseline.transaction_ids.map((id) => ({ type: "transaction" as const, id })))
    }
  }
  for (const match of [fraud?.primary, ...(fraud?.alternatives ?? []), ...(fraud?.leads ?? [])]) {
    if (match) refs.push(...match.current_evidence, ...match.historical_evidence)
  }
  return [...new Map(refs.map((ref) => [evidenceKey(ref), ref])).values()]
}

function pathRecords(path?: Path): EvidenceRef[] {
  if (!path) return []
  const current = path.steps[0]?.end
  const historical = path.steps.at(-1)?.start
  if (current?.kind !== "transaction" || historical?.kind !== "transaction" ||
    !current.source_id || !historical.source_id) return []
  return [{ type: "transaction", id: current.source_id }, { type: "transaction", id: historical.source_id },
    { type: "payout", id: path.seed_payout_id }]
}

export function OverrideDialog({ detail, effective: displayedDecision, onClose, onResult }: {
  detail: Investigation; effective: EffectiveDecision; onClose: () => void
  onResult: (result: "applied" | "stale") => void
}) {
  const { prefix } = useWorkspace()
  const client = useQueryClient()
  const [effective] = useState(displayedDecision)
  const dialog = useRef<HTMLDialogElement>(null)
  const submitLock = useRef(false)
  const [decision, setDecision] = useState<Decision | "">("")
  const [reason, setReason] = useState("")
  const [clusterId, setClusterId] = useState("")
  const [pathIndex, setPathIndex] = useState(0)
  const [selected, setSelected] = useState<Set<string>>(() => new Set())
  const [search, setSearch] = useState("")
  const [confirming, setConfirming] = useState(false)
  const [invalid, setInvalid] = useState("")
  const clusters = useRiskQuery(effective.run_id, ["clusters"], (signal) => getClusters(effective.run_id, signal))
  const allowed = publishedRecords(detail, effective)
  const supportedClusters = detail.fraud?.established_exposure ? (clusters.data ?? []).flatMap((cluster) => {
    const association = cluster.associated_payouts.find((payout) => payout.payout_id === effective.payout_id)
    const paths = association?.paths.filter((path) => pathRecords(path).length > 0) ?? []
    return paths.length ? [{ cluster, paths }] : []
  }) : []
  const activeCluster = supportedClusters.find((item) => item.cluster.cluster_id === clusterId)
  const mandatory = decision === "REJECT" && activeCluster ? pathRecords(activeCluster.paths[pathIndex]) : []
  const requiredKeys = new Set(mandatory.map(evidenceKey))
  const selectable = allowed.filter((ref) => decision !== "MANUAL_REVIEW" || (ref.type !== "team" &&
    (ref.type !== "payout" || ref.id !== effective.payout_id)))
  const evidence = [...new Map([...mandatory, ...selectable.filter((ref) => selected.has(evidenceKey(ref)))]
    .map((ref) => [evidenceKey(ref), ref])).values()]

  const mutation = useMutation({ mutationFn: () => {
    if (!decision) throw new Error("A decision is required")
    return overridePayout(effective.payout_id, {
      run_id: effective.run_id, expected_version: effective.effective_version,
      decision, reason: reason.trim(), evidence, cluster_id: decision === "REJECT" ? clusterId : null,
    })
  }, onSuccess: async () => {
    await client.invalidateQueries({ queryKey: riskQueryKey(prefix, effective.run_id) })
    dialog.current?.close()
    onResult("applied")
  }, onError: async (error) => {
    if (error instanceof ApiError && error.status === 409) {
      await client.invalidateQueries({ queryKey: riskQueryKey(prefix, effective.run_id) })
      dialog.current?.close()
      onResult("stale")
    }
  }, onSettled: () => { submitLock.current = false } })

  useEffect(() => { dialog.current?.showModal() }, [])
  const visible = selectable.filter((ref) => evidenceKey(ref).toLowerCase().includes(search.trim().toLowerCase()))

  function review(event: FormEvent) {
    event.preventDefault()
    if (!decision || !reason.trim()) { setInvalid("Choose a decision and enter a reason."); return }
    if (decision === "MANUAL_REVIEW" && !evidence.length) { setInvalid("Select a supporting transaction or historical payout."); return }
    if (decision === "REJECT" && !mandatory.length) { setInvalid("Select a cluster and a published exposure path."); return }
    if (evidence.length > 100) { setInvalid("Select no more than 100 supporting records."); return }
    setInvalid("")
    setConfirming(true)
  }

  return <dialog ref={dialog} className="override-dialog" aria-labelledby="override-heading"
    onClose={onClose} onCancel={(event) => { if (mutation.isPending) event.preventDefault() }}>
    <div className="dialog-inner">
      <div className="section-heading">
        <h2 id="override-heading">{confirming ? "Confirm decision override" : "Override decision"}</h2>
        <button type="button" aria-label="Close override" className="icon-button"
          disabled={mutation.isPending} onClick={() => dialog.current?.close()}><X className="size-5" /></button>
      </div>
      <p className="text-sm text-muted-foreground">Payout {effective.payout_id} · Current version {effective.effective_version}</p>
      {mutation.isError && <Notice kind="error">{errorMessage(mutation.error)}</Notice>}
      {confirming && decision ? <>
        <StatusBadge status={decision} />
        <p className="break-words text-sm">{reason.trim()}</p>
        <Evidence records={evidence} />
        <p className="text-sm text-muted-foreground">Confirmation appends an audit event and retains the original automated decision.</p>
        <div className="dialog-actions">
          <Button variant="secondary" disabled={mutation.isPending} onClick={() => { mutation.reset(); setConfirming(false) }}>Back</Button>
          <Button disabled={mutation.isPending} onClick={() => {
            if (submitLock.current) return
            submitLock.current = true
            mutation.mutate()
          }}>{mutation.isPending ? "Submitting…" : "Confirm override"}</Button>
        </div>
      </> : <form className="grid min-w-0 gap-4" onSubmit={review} noValidate>
        {invalid && <Notice kind="error">{invalid}</Notice>}
        <label className="field"><span>New decision</span>
          <select required value={decision} onChange={(event) => {
            setDecision(event.target.value as Decision | ""); setInvalid(""); setClusterId(""); setPathIndex(0); setSelected(new Set())
          }}>
            <option value="">Choose a decision</option>
            {(["APPROVE", "MANUAL_REVIEW", "REJECT"] as const).map((value) => <option key={value} value={value}
              disabled={value === effective.effective_decision || (value === "REJECT" && !supportedClusters.length)}>
              {labelOf(value)}
            </option>)}
          </select>
        </label>
        {detail.fraud?.established_exposure && clusters.isError &&
          <RetryNotice message={errorMessage(clusters.error)} onRetry={() => void clusters.refetch()} />}
        {decision === "REJECT" && <>
          <label className="field"><span>Same-run fraud cluster</span>
            <select value={clusterId} onChange={(event) => { setClusterId(event.target.value); setPathIndex(0) }}>
              <option value="">Select a cluster</option>
              {supportedClusters.map(({ cluster }) => <option key={cluster.cluster_id} value={cluster.cluster_id}>{cluster.stable_case_key}</option>)}
            </select></label>
          {activeCluster && <label className="field"><span>Published exposure path</span>
            <select value={pathIndex} onChange={(event) => setPathIndex(Number(event.target.value))}>
              {activeCluster.paths.map((path, index) => <option key={index} value={index}>
                Path {index + 1} · {path.seed_payout_id}
              </option>)}
            </select></label>}
        </>}
        <label className="field"><span>Override reason</span>
          <textarea className="risk-textarea" required value={reason} maxLength={1000} rows={3}
            onChange={(event) => setReason(event.target.value)} />
        </label>
        {decision && <fieldset className="min-w-0">
          <legend className="mb-2 text-sm font-semibold">Supporting evidence{decision === "APPROVE" ? " (optional)" : ""}</legend>
          <label className="field"><span className="sr-only">Search evidence</span>
            <input type="search" placeholder="Find a source record" value={search} onChange={(event) => setSearch(event.target.value)} />
          </label>
          {mandatory.length > 0 && <div className="my-2"><p className="text-xs">Required records from the selected exposure path</p><Evidence records={mandatory} /></div>}
          <div className="evidence-picker">
            {visible.map((ref) => {
              const key = evidenceKey(ref)
              return <label key={key}><input type="checkbox" checked={selected.has(key) || requiredKeys.has(key)}
                disabled={requiredKeys.has(key)} onChange={(event) => {
                  const checked = event.target.checked
                  setSelected((previous) => { const next = new Set(previous); if (checked) next.add(key); else next.delete(key); return next })
                }} /> <span>{ref.type} <code>{ref.id}</code></span></label>
            })}
            {!visible.length && <p className="text-xs text-muted-foreground">No matching records.</p>}
          </div>
          <p className="mt-2 text-xs text-muted-foreground">{evidence.length} selected</p>
        </fieldset>}
        <div className="dialog-actions"><Button type="submit">Review change</Button></div>
      </form>}
    </div>
  </dialog>
}
