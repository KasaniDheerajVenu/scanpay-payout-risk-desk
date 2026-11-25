import { apiUrl } from "./api-origin"

export type HealthResponse = {
  status: "ok"
}

export async function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  const response = await fetch(apiUrl("/health"), {
    headers: { Accept: "application/json" },
    signal,
    cache: "no-store",
  })

  if (!response.ok) {
    throw new Error("The API is unavailable.")
  }

  const payload: unknown = await response.json()
  if (typeof payload !== "object" || payload === null || !("status" in payload) || payload.status !== "ok") {
    throw new Error("The API returned an unexpected response.")
  }

  return { status: payload.status }
}
