import { useQuery } from "@tanstack/react-query"
import { useSearchParams } from "react-router"
import { useWorkspace } from "@/features/auth/session"
import { getRun, runKey, type Evaluation } from "@/services/workspace"

export function contextSearch(params: URLSearchParams) {
  const context = new URLSearchParams()
  for (const key of ["batch_id", "run_id"]) {
    const value = params.get(key)
    if (value) context.set(key, value)
  }
  return context.size ? `?${context}` : ""
}

export function useRunSelection() {
  const [params, setParams] = useSearchParams()
  const { prefix } = useWorkspace()
  const selectedId = params.get("run_id")
  const selected = useQuery({
    queryKey: [...prefix, ...runKey, "detail", selectedId],
    queryFn: ({ signal }) => getRun(selectedId!, signal),
    enabled: !!selectedId,
    retry: false,
  })
  return {
    selectedId,
    selected,
    setSelected(run: Evaluation) {
      if (run.status !== "COMPLETED") return
      setParams((previous) => {
        const next = new URLSearchParams(previous)
        next.set("run_id", run.run_id)
        return next
      })
    },
  }
}
