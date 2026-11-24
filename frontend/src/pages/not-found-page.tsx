import { Link } from "react-router"
import { Card } from "@/components/ui/card"

export function NotFoundPage() {
  return (
    <Card className="grid gap-4 p-5 sm:p-6">
      <h1 className="text-2xl font-semibold tracking-tight">Page not found</h1>
      <Link className="w-fit rounded-sm font-semibold text-primary underline" to="/">
        Go to application status
      </Link>
    </Card>
  )
}
