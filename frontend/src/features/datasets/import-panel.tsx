import { FileSpreadsheet, Upload, X } from "lucide-react"
import { useMutation } from "@tanstack/react-query"
import { useRef, useState, type FormEvent } from "react"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Notice } from "@/components/ui/workspace"
import { formatBytes } from "@/lib/format"
import { ApiError, errorMessage } from "@/services/api"
import { demoImport, uploadBatch, type BatchDetail, type FileType, type ImportMode } from "@/services/workspace"
import { defaultTotalLimit, fileLabels, fileProblem, fileTypes, makeUpload, requiredFiles, type UploadFiles } from "./files"

export function ValidationLocation({ file, row, field }: {
  file?: string | null; row?: number | null; field?: string | null
}) {
  const fileName = file && Object.hasOwn(fileLabels, file) ? fileLabels[file as FileType] : null
  const parts = [fileName, row != null ? `Row ${row}` : null, field ? `Field ${field}` : null].filter(Boolean)
  return parts.length ? <p className="mt-1 text-xs break-words">{parts.join(" · ")}</p> : null
}

function ImportError({ error }: { error: unknown }) {
  return <Notice kind="error">
    {errorMessage(error, "import")}
    {error instanceof ApiError && <ValidationLocation file={error.details.file_type}
      row={error.details.row} field={error.details.field} />}
  </Notice>
}

function FileSlot({ type, required, file, disabled, onChange }: {
  type: FileType; required: boolean; file?: File; disabled: boolean; onChange: (file?: File) => void
}) {
  const [dropError, setDropError] = useState("")
  const validation = file ? fileProblem(file) : undefined
  const name = fileLabels[type]
  return (
    <div className="file-slot" onDragOver={(event) => event.preventDefault()} onDrop={(event) => {
      event.preventDefault()
      if (disabled) return
      const list = event.dataTransfer.files
      if (list.length !== 1) { setDropError("Drop one CSV file in this field."); return }
      setDropError("")
      onChange(list[0])
    }}>
      <div className="file-meta">
        <FileSpreadsheet className="size-4 shrink-0 text-primary" aria-hidden="true" />
        <div className="min-w-0">
          <strong>{name}</strong> <small>{required ? "Required" : "Optional"}</small>
          {file && <p className="break-all text-xs text-muted-foreground">{file.name} · {formatBytes(file.size)}</p>}
        </div>
      </div>
      <div className="file-actions">
        <input id={`upload-${type}`} type="file" accept=".csv,text/csv" className="peer sr-only"
          aria-label={name} disabled={disabled} onChange={(event) => {
            setDropError("")
            const selected = event.target.files?.[0]
            if (selected) onChange(selected)
            event.target.value = ""
          }} />
        <label htmlFor={`upload-${type}`} className="file-pick">{file ? "Replace" : "Choose CSV"}</label>
        {file && <button type="button" className="icon-button" aria-label={`Remove ${name}`} disabled={disabled}
          onClick={() => { setDropError(""); onChange() }}><X className="size-4" aria-hidden="true" /></button>}
      </div>
      {(validation || dropError) && <p role="alert" className="w-full text-xs text-destructive">{validation || dropError}</p>}
    </div>
  )
}

type Completion = (batch: BatchDetail, reused: boolean) => void
type ImportPanelProps = { onImported: Completion; onFailed: () => void }

export function UploadPanel({ onImported, onFailed }: ImportPanelProps) {
  const [mode, setMode] = useState<ImportMode>("INITIAL")
  const [cutoff, setCutoff] = useState("")
  const [files, setFiles] = useState<UploadFiles>({})
  const [assessment, setAssessment] = useState(false)
  const lock = useRef(false)
  const mutation = useMutation({ mutationFn: uploadBatch, gcTime: 0 })
  const required = requiredFiles(mode)
  const tooLarge = Object.values(files).reduce((total, file) => total + (file?.size ?? 0), 0) > defaultTotalLimit
  const valid = !!cutoff && required.every((type) => files[type])
    && !Object.values(files).some((file) => file && fileProblem(file)) && !tooLarge
  const { isPending } = mutation

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (lock.current || !valid) return
    lock.current = true
    mutation.mutate(makeUpload(mode, cutoff, files, assessment), {
      onSuccess: ({ data, status }) => { setFiles({}); onImported(data, status === 200) },
      onError: onFailed,
      onSettled: () => { lock.current = false },
    })
  }

  return (
    <Card className="import-panel">
      <div className="section-heading"><h2>Import CSV dataset</h2></div>
      <form className="grid gap-4" onSubmit={submit}>
        <div className="form-grid">
          <label className="field"><span>Import mode</span>
            <select value={mode} disabled={isPending} onChange={(event) => setMode(event.target.value as ImportMode)}>
              <option value="INITIAL">Initial</option><option value="INCREMENTAL">Incremental</option>
            </select>
          </label>
          <label className="field"><span>Evaluation cutoff (IST)</span>
            <input type="datetime-local" value={cutoff} required disabled={isPending}
              onChange={(event) => setCutoff(event.target.value)} />
          </label>
        </div>
        {mode === "INITIAL" && <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-0.5 size-4 accent-primary" checked={assessment} disabled={isPending}
            onChange={(event) => setAssessment(event.target.checked)} /> Enforce assessment counts
        </label>}
        <p className="text-sm text-muted-foreground">Choose or drop one CSV per field. Default limits are 4 MB per file and 16 MB in total.</p>
        <div className="grid gap-2.5">
          {fileTypes.map((type) => <FileSlot key={type} type={type} file={files[type]} disabled={isPending}
            required={required.includes(type)} onChange={(file) => setFiles((old) => ({ ...old, [type]: file }))} />)}
        </div>
        {tooLarge && <Notice kind="error">The selected files exceed 16 MB in total.</Notice>}
        {mutation.isError && <ImportError error={mutation.error} />}
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" disabled={!valid || isPending}><Upload className="size-4" aria-hidden="true" />
            {isPending ? "Uploading and validating…" : mutation.isError ? "Retry upload" : "Upload dataset"}
          </Button>
          {isPending && <span role="status" className="text-sm text-muted-foreground">Waiting for the import result.</span>}
        </div>
      </form>
    </Card>
  )
}

export function DemoImportPanel({ onImported, onFailed }: ImportPanelProps) {
  const [preset, setPreset] = useState("synthetic-v1")
  const lock = useRef(false)
  const mutation = useMutation({ mutationFn: demoImport })
  return (
    <Card className="import-panel">
      <div className="section-heading"><h2>Import synthetic preset</h2></div>
      <form className="grid gap-4" onSubmit={(event) => {
        event.preventDefault()
        if (lock.current) return
        lock.current = true
        mutation.mutate(preset, {
          onSuccess: ({ data, status }) => onImported(data, status === 200),
          onError: onFailed,
          onSettled: () => { lock.current = false },
        })
      }}>
        <label className="field"><span>Synthetic dataset</span>
          <select value={preset} disabled={mutation.isPending} onChange={(event) => setPreset(event.target.value)}>
            <option value="synthetic-v1">Synthetic dataset v1</option>
          </select>
        </label>
        {mutation.isError && <ImportError error={mutation.error} />}
        <Button type="submit" disabled={mutation.isPending} className="w-fit">
          {mutation.isPending ? "Importing preset…" : mutation.isError ? "Retry preset import" : "Import preset"}
        </Button>
      </form>
    </Card>
  )
}
