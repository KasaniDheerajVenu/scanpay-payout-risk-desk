/// <reference types="vite/client" />

const configuredUrl = import.meta.env.VITE_API_URL?.trim()

function apiOrigin(value: string | undefined): string {
  if (!value) return ""
  const url = new URL(value)
  if ((url.protocol !== "https:" && url.protocol !== "http:") || url.username || url.password ||
    url.pathname !== "/" || url.search || url.hash) {
    throw new Error("VITE_API_URL must be an HTTP or HTTPS origin without a path.")
  }
  return url.origin
}

const origin = apiOrigin(configuredUrl)

export function apiUrl(path: `/api/${string}` | "/health") {
  return `${origin}${path}`
}

export const apiCredentials: RequestCredentials = origin ? "include" : "same-origin"
