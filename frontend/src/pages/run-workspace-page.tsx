import { Activity } from "lucide-react"
import { Link, useSearchParams } from "react-router"
import { Card } from "@/components/ui/card"
import { EmptyPanel, LoadingPanel, PageHeader, StatusBadge } from "@/components/ui/workspace"
import { contextSearch, useRunSelection } from "@/features/evaluations/selection"
import { formatIST } from "@/lib/format"

export function RunWorkspacePage({ title }: { title: string }) {
  const [params] = useSearchParams()
  const { selected, selectedId } = useRunSelection()
  const to = "/evaluations" + contextSearch(params)
  return (
    <>
      <PageHeader title={title} description="Run context for risk investigation." />
      {selectedId && selected.isPending ? <LoadingPanel label="Loading selected run" /> :
        <Card className="p-5">
          {selected.data?.status === "COMPLETED"
            ? <><div className="section-heading"><h2>Selected evaluation</h2><StatusBadge status={selected.data.status} /></div>
              <code className="break-all text-sm">{selected.data.run_id}</code>
              <p className="mt-2 text-sm text-muted-foreground">Cutoff {formatIST(selected.data.cutoff_at)}</p></>
            : <EmptyPanel icon={Activity} title="Select a completed evaluation">
              Choose a run from evaluation history to set the investigation context.
            </EmptyPanel>}
          <Link className="text-link mt-4 inline-block" to={to}>Go to evaluations</Link>
        </Card>}
    </>
  )
}
