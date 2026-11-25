import { ArrowRight, Eye, EyeOff, FileSpreadsheet, Play, ShieldCheck } from "lucide-react"
import { useRef, useState, type FormEvent } from "react"
import { Navigate, useLocation } from "react-router"
import { Button } from "@/components/ui/button"
import { Notice } from "@/components/ui/workspace"
import { useAuth } from "@/features/auth/session"
import { errorMessage } from "@/services/api"

export function LoginPage() {
  const auth = useAuth()
  const location = useLocation()
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [visible, setVisible] = useState(false)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState("")
  const lock = useRef(false)
  const from: unknown = location.state?.from
  const destination = typeof from === "string" && from.startsWith("/") && !from.startsWith("//")
    ? from : "/datasets"

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (lock.current) return
    if (!username.trim() || !password) {
      setFailure("Enter your username and password.")
      return
    }
    lock.current = true
    setBusy(true)
    setFailure("")
    try {
      await auth.login(username.trim(), password)
    } catch (error) {
      setFailure(errorMessage(error, "login"))
      setPassword("")
    } finally {
      lock.current = false
      setBusy(false)
    }
  }

  if (auth.session) return <Navigate to={destination} replace />
  return (
    <main className="login-shell">
      <aside className="login-rail" aria-label="About the risk desk">
        <div className="login-brand">
          <span className="brand-mark"><ShieldCheck className="size-5" aria-hidden="true" /></span>
          <span>ScanPay <small>Payout Risk Desk</small></span>
        </div>
        <div className="login-rail-copy">
          <h2>Payout operations</h2>
          <p>Prepare datasets and evaluate payout risk in one workspace.</p>
          <div className="login-steps">
            <span><FileSpreadsheet className="size-4" aria-hidden="true" /> Review dataset readiness</span>
            <span><Play className="size-4" aria-hidden="true" /> Follow evaluation runs</span>
          </div>
        </div>
      </aside>
      <div className="login-main">
        <div className="login-form">
          <div className="mobile-brand"><ShieldCheck className="size-5" aria-hidden="true" /> ScanPay Risk Desk</div>
          <h1>Sign in</h1>
          <p className="mb-7 mt-2 text-sm text-muted-foreground">Use your workspace credentials to continue.</p>
          {auth.expired && <div className="mb-4"><Notice>Your session expired. Sign in to continue.</Notice></div>}
          {failure && <div className="mb-4"><Notice kind="error">{failure}</Notice></div>}
          <form className="grid gap-4" onSubmit={(event) => void submit(event)} noValidate>
            <label className="field">
              <span>Username</span>
              <input value={username} onChange={(event) => setUsername(event.target.value)} required
                autoComplete="username" maxLength={64} autoCapitalize="none" disabled={busy} />
            </label>
            <div className="field">
              <label htmlFor="workspace-password">Password</label>
              <div className="password-field">
                <input id="workspace-password" type={visible ? "text" : "password"} value={password}
                  onChange={(event) => setPassword(event.target.value)} required
                  autoComplete="current-password" maxLength={128} disabled={busy} />
                <button className="icon-button" type="button"
                  aria-label={visible ? "Hide password" : "Show password"}
                  onClick={() => setVisible(!visible)}>
                  {visible ? <EyeOff className="size-4" aria-hidden="true" /> : <Eye className="size-4" aria-hidden="true" />}
                </button>
              </div>
            </div>
            <Button type="submit" disabled={busy} className="mt-1 justify-between">
              {busy ? "Signing in…" : "Sign in"} <ArrowRight className="size-4" aria-hidden="true" />
            </Button>
          </form>
        </div>
      </div>
    </main>
  )
}
