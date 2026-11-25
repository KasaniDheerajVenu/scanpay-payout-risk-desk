import { CircleAlert, CircleCheck, RefreshCw, type LucideIcon } from "lucide-react"
import type { ReactNode } from "react"
import { labelOf } from "@/lib/format"
import { cn } from "@/lib/utils"
import { Button } from "./button"
import { Card } from "./card"

export function PageHeader({ title, description, action }: {
  title: string; description: string; action?: ReactNode
}) {
  return (
    <div className="page-heading">
      <div><h1>{title}</h1><p>{description}</p></div>
      {action && <div className="page-action">{action}</div>}
    </div>
  )
}

export function StatusBadge({ status }: { status: string }) {
  const key = status.toUpperCase()
  const tone = ["READY", "COMPLETED", "ACTIVE", "APPROVE", "PASSED", "CURRENT", "CAPTURED"].includes(key) ? "good"
    : ["FAILED", "REJECT", "VALIDATION_FAILED", "INFERENCE_FAILED", "ESTABLISHED_EXPOSURE", "DECLINED"].includes(key) ? "bad"
      : ["RUNNING", "VALIDATING", "QUEUED", "MANUAL_REVIEW", "DIAGNOSTIC_ONLY", "ANOMALY", "REFUNDED", "UNRESOLVED_RISK"].includes(key) ? "attention"
        : "neutral"
  return <span className={cn("status-badge", `status-${tone}`)}><i aria-hidden="true" />{labelOf(status)}</span>
}

export function Notice({ kind = "info", children, title }: {
  kind?: "info" | "error" | "success"; children: ReactNode; title?: string
}) {
  const Icon = kind === "success" ? CircleCheck : CircleAlert
  return (
    <div className={`notice notice-${kind}`} role={kind === "error" ? "alert" : undefined}>
      <Icon aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
      <div className="min-w-0">{title && <strong className="block">{title}</strong>}{children}</div>
    </div>
  )
}

export function LoadingPanel({ label }: { label: string }) {
  return (
    <Card className="p-6" role="status" aria-label={label}>
      <span className="sr-only">{label}</span>
      <div aria-hidden="true" className="grid gap-4 motion-safe:animate-pulse">
        <div className="h-5 w-40 max-w-full rounded bg-muted" />
        <div className="h-11 rounded bg-muted/65" />
        <div className="h-11 rounded bg-muted/50" />
      </div>
    </Card>
  )
}

export function EmptyPanel({ icon: Icon, title, children }: {
  icon: LucideIcon; title: string; children: ReactNode
}) {
  return (
    <div className="empty-panel">
      <span className="empty-icon"><Icon className="size-5" aria-hidden="true" /></span>
      <div><h3>{title}</h3><p>{children}</p></div>
    </div>
  )
}

export function RetryNotice({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <Notice kind="error">
      <p>{message}</p>
      <Button variant="secondary" onClick={onRetry} className="mt-2 min-h-9">
        <RefreshCw className="size-4" aria-hidden="true" /> Retry
      </Button>
    </Notice>
  )
}
