import {
  Activity, ArrowUpRight, ChevronLeft, ChevronRight, CircleHelp, Database, FlaskConical,
  FolderSearch, LayoutDashboard, ListChecks, LogOut, Menu, ShieldCheck, X,
} from "lucide-react"
import { useRef, useState } from "react"
import { Link, NavLink, Outlet, useSearchParams } from "react-router"
import { Notice } from "@/components/ui/workspace"
import { hasPermission, useAuth, useWorkspace } from "@/features/auth/session"
import { contextSearch, useRunSelection } from "@/features/evaluations/selection"
import { labelOf } from "@/lib/format"
import { errorMessage } from "@/services/api"

const navigation = [
  { path: "/datasets", label: "Datasets", icon: Database },
  { path: "/evaluations", label: "Evaluations", icon: Activity },
  { path: "/dashboard", label: "Overview", icon: LayoutDashboard },
  { path: "/payouts", label: "Payout queue", icon: ListChecks },
  { path: "/fraud", label: "Fraud insights", icon: FolderSearch },
  { path: "/models", label: "Model insights", icon: FlaskConical },
] as const

function Brand({ href }: { href: string }) {
  return (
    <Link to={href} className="sidebar-brand" aria-label="ScanPay datasets">
      <span className="brand-mark"><ShieldCheck className="size-5" aria-hidden="true" /></span>
      <span className="sidebar-wordmark">ScanPay<small>Payout Risk Desk</small></span>
    </Link>
  )
}

function Navigation({ search, onNavigate }: { search: string; onNavigate?: () => void }) {
  return (
    <nav aria-label="Workspace">
      <div className="sidebar-section">Workspace</div>
      {navigation.map(({ path, label, icon: Icon }) => (
        <NavLink key={path} to={path + search} className="nav-item" title={label} onClick={onNavigate}>
          <Icon className="size-[18px] shrink-0" aria-hidden="true" />
          <span className="nav-label">{label}</span>
        </NavLink>
      ))}
    </nav>
  )
}

export function AppLayout() {
  const { user } = useWorkspace()
  const { logout } = useAuth()
  const [params] = useSearchParams()
  const search = contextSearch(params)
  const { selected, selectedId } = useRunSelection()
  const [collapsed, setCollapsed] = useState(false)
  const [leaving, setLeaving] = useState(false)
  const [failure, setFailure] = useState("")
  const drawer = useRef<HTMLDialogElement>(null)
  const initials = user.display_name.split(/\s+/).slice(0, 2).map((part) => part.charAt(0)).join("")
  const canRead = hasPermission(user, "read_risk")

  async function leave() {
    if (leaving) return
    setLeaving(true)
    setFailure("")
    try { await logout() }
    catch (error) { setFailure(errorMessage(error)) }
    finally { setLeaving(false) }
  }

  return (
    <div className={collapsed ? "workspace sidebar-collapsed" : "workspace"}>
      <a className="skip-link" href="#main-content">Skip to content</a>
      <aside className="desktop-sidebar">
        <Brand href={"/datasets" + search} />
        {canRead && <Navigation search={search} />}
        <div className="sidebar-footer">
          <Link to="/status" title="Application status" className="nav-item">
            <CircleHelp aria-hidden="true" className="size-[18px] shrink-0" />
            <span className="nav-label">Application status</span>
          </Link>
          <button type="button" className="nav-item" title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"} aria-expanded={!collapsed}
            onClick={() => setCollapsed(!collapsed)}>
            {collapsed ? <ChevronRight className="size-[18px]" aria-hidden="true" />
              : <ChevronLeft className="size-[18px]" aria-hidden="true" />}
            <span className="nav-label">Collapse sidebar</span>
          </button>
        </div>
      </aside>
      <dialog ref={drawer} className="navigation-drawer" aria-label="Workspace navigation">
        <div className="flex items-center justify-between pr-3">
          <Brand href={"/datasets" + search} />
          <button type="button" className="icon-button" aria-label="Close navigation"
            onClick={() => drawer.current?.close()}><X className="size-5" aria-hidden="true" /></button>
        </div>
        {canRead && <Navigation search={search} onNavigate={() => drawer.current?.close()} />}
      </dialog>
      <div className="workspace-content">
        <header className="workspace-header">
          <div className="flex min-w-0 items-center gap-2">
            <button type="button" className="icon-button mobile-menu" aria-label="Open navigation"
              onClick={() => drawer.current?.showModal()}><Menu className="size-5" aria-hidden="true" /></button>
            <span className="header-title">Risk operations</span>
            {user.mode === "demo" && <span className="demo-chip">Synthetic demo</span>}
          </div>
          <div className="profile">
            <span className="profile-avatar" aria-hidden="true">{initials}</span>
            <div className="profile-label">
              <strong title={user.display_name}>{user.display_name}</strong>
              <small>{labelOf(user.role)}</small>
            </div>
            <button type="button" className="icon-button logout-button" aria-label="Sign out"
              title="Sign out" disabled={leaving} onClick={() => void leave()}>
              <LogOut className="size-4" aria-hidden="true" />
            </button>
          </div>
        </header>
        {canRead && <div className="run-strip">
          <span><span className="run-strip-label">Selected run</span>{" "}
            {selected.data?.status === "COMPLETED"
              ? <code title={selected.data.run_id}>{selected.data.run_id}</code>
              : selectedId ? selected.isFetching ? "Loading…" : "Unavailable" : "None selected"}
          </span>
          <Link to={"/evaluations" + search}>View evaluations <ArrowUpRight className="size-3.5" aria-hidden="true" /></Link>
        </div>}
        <main id="main-content" tabIndex={-1} className="workspace-main">
          {failure && <Notice kind="error">{failure}</Notice>}
          <Outlet />
        </main>
      </div>
    </div>
  )
}
