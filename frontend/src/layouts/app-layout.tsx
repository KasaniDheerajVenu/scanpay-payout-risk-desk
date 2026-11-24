import { ShieldCheck } from "lucide-react"
import { Link, Outlet } from "react-router"

export function AppLayout() {
  return (
    <div className="min-h-svh">
      <a
        className="sr-only z-10 rounded-md bg-card p-3 focus:not-sr-only focus:absolute"
        href="#main-content"
      >
        Skip to content
      </a>
      <header className="bg-sidebar text-sidebar-foreground">
        <div className="mx-auto flex min-h-16 max-w-5xl items-center px-5 sm:px-8">
          <Link to="/" className="inline-flex items-center gap-3 rounded-sm text-sm font-semibold">
            <ShieldCheck aria-hidden="true" className="size-5 shrink-0" />
            Payout Risk Desk
          </Link>
        </div>
      </header>
      <main id="main-content" tabIndex={-1} className="mx-auto max-w-5xl px-5 py-8 sm:px-8 sm:py-12">
        <Outlet />
      </main>
    </div>
  )
}
