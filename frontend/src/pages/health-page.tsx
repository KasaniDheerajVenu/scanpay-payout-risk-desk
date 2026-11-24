import { useQuery } from "@tanstack/react-query"
import { CircleAlert, CircleCheck, CircleDashed, RefreshCw } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { getHealth } from "@/services/health"

export function HealthPage() {
  const health = useQuery({
    queryKey: ["health"],
    queryFn: ({ signal }) => getHealth(signal),
    retry: false,
  })

  return (
    <div className="grid gap-5">
      <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">Application status</h1>
      <Card aria-labelledby="api-health" className="grid gap-5 p-5 sm:grid-cols-[1fr_auto] sm:p-6">
        <div className="min-w-0">
          <h2 id="api-health" className="mb-2 text-sm font-semibold">Backend connection</h2>
          <div role="status" aria-atomic="true" className="text-sm">
            {health.isFetching ? (
              <span className="inline-flex items-center gap-2 text-muted-foreground">
                <CircleDashed aria-hidden="true" className="size-4" /> Checking connection…
              </span>
            ) : health.isSuccess ? (
              <span className="inline-flex items-center gap-2 rounded-md bg-success-surface px-2 py-1 text-success">
                <CircleCheck aria-hidden="true" className="size-4" /> Connected
              </span>
            ) : (
              <span className="inline-flex items-center gap-2 rounded-md bg-error-surface px-2 py-1 text-destructive">
                <CircleAlert aria-hidden="true" className="size-4" /> API unavailable
              </span>
            )}
          </div>
        </div>
        <Button
          disabled={health.isFetching}
          onClick={() => void health.refetch()}
          className="w-full sm:w-auto sm:self-center"
        >
          <RefreshCw aria-hidden="true" className="size-4" />
          Check connection
        </Button>
      </Card>
    </div>
  )
}
