import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { MemoryRouter } from "react-router"
import { afterEach, describe, expect, it, vi } from "vitest"
import { App } from "@/App"

function renderApp(path = "/status") {
  const client = new QueryClient({ defaultOptions: { queries: { gcTime: 0 } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe("application health", () => {
  it("loads health from the same origin and shows the returned status", async () => {
    const request = vi.fn().mockResolvedValue(Response.json({ status: "ok" }))
    vi.stubGlobal("fetch", request)

    renderApp()

    const status = screen.getByRole("status")
    expect(within(status).getByText("Checking connection…")).toBeTruthy()
    expect(await within(status).findByText("Connected")).toBeTruthy()
    expect(request).toHaveBeenCalledWith("/health", expect.objectContaining({
      headers: { Accept: "application/json" },
    }))
  })

  it("can recover from an unavailable backend when the user retries", async () => {
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(new Response(null, { status: 503 }))
      .mockResolvedValueOnce(Response.json({ status: "ok" })))

    renderApp()

    await screen.findByText("API unavailable")
    fireEvent.click(screen.getByRole("button", { name: "Check connection" }))
    expect(await screen.findByText("Connected")).toBeTruthy()
  })

  it("rejects an unexpected health payload", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ status: "unknown" })))

    renderApp()

    expect(await screen.findByText("API unavailable")).toBeTruthy()
  })

  it("provides a way back from unknown client routes", () => {
    renderApp("/missing")

    expect(screen.getByRole("heading", { name: "Page not found" })).toBeTruthy()
    expect(screen.getByRole("link", { name: "Go to application status" }).getAttribute("href"))
      .toBe("/status")
  })
})
