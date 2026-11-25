import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { advanceSession, onSessionExpiry } from "@/services/api"
import { getSession, sessionKey, signIn, signOut, type Permission, type Session } from "@/services/workspace"

type Auth = {
  session: Session | null | undefined
  error: unknown
  expired: boolean
  reload: () => void
  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
}

const SessionContext = createContext<Auth | null>(null)
const isPrivate = { predicate: ({ queryKey }: { queryKey: readonly unknown[] }) => queryKey[0] === "workspace" }

export function SessionProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const session = useQuery({ queryKey: sessionKey, queryFn: ({ signal }) => getSession(signal), retry: false })
  const [expired, setExpired] = useState(false)
  const previous = useRef<Session | null>(null)

  const clearPrivate = useCallback(() => {
    void queryClient.cancelQueries(isPrivate)
    queryClient.removeQueries(isPrivate)
    queryClient.getMutationCache().clear()
  }, [queryClient])

  const replaceSession = useCallback(async (next: Session | null) => {
    advanceSession()
    await queryClient.cancelQueries({ queryKey: sessionKey })
    clearPrivate()
    previous.current = next
    queryClient.setQueryData(sessionKey, next)
  }, [queryClient, clearPrivate])

  useEffect(() => onSessionExpiry(() => {
    setExpired(true)
    void replaceSession(null)
  }), [replaceSession])

  useEffect(() => {
    if (session.data === undefined) return
    if (previous.current && session.data?.user_id !== previous.current.user_id) {
      advanceSession()
      clearPrivate()
    }
    previous.current = session.data
  }, [session.data, clearPrivate])

  return (
    <SessionContext.Provider value={{
      session: session.data,
      error: session.error,
      expired,
      reload: () => { void session.refetch() },
      login: async (username, password) => {
        await replaceSession(await signIn(username, password))
        setExpired(false)
      },
      logout: async () => {
        await signOut()
        await replaceSession(null)
        setExpired(false)
      },
    }}>
      {children}
    </SessionContext.Provider>
  )
}

export function useAuth() {
  const value = useContext(SessionContext)
  if (!value) throw new Error("SessionProvider is required")
  return value
}

export const hasPermission = (user: Session, permission: Permission) => user.permissions.includes(permission)

export function useWorkspace() {
  const { session } = useAuth()
  if (!session) throw new Error("A signed-in session is required")
  return { user: session, prefix: ["workspace", session.user_id] as const }
}
