const integer = new Intl.NumberFormat("en-US")
const dollars = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" })
const ist = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Asia/Kolkata", day: "numeric", month: "short", year: "numeric",
  hour: "2-digit", minute: "2-digit", hourCycle: "h23",
})

export const formatNumber = (value: number) => integer.format(value)
export const formatCents = (cents: number) => dollars.format(cents / 100)
export const formatIST = (value: string | null) => value ? `${ist.format(new Date(value))} IST` : "—"
const dateOnly = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Kolkata", day: "numeric", month: "short", year: "numeric" })
export const formatDate = (value: string) => dateOnly.format(new Date(`${value}T00:00:00+05:30`))
export function formatBytes(value: number) {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / (1024 * 1024)).toFixed(1)} MB`
}
export const labelOf = (value: string) => value.toLowerCase().split("_").map(
  (part) => part.charAt(0).toUpperCase() + part.slice(1),
).join(" ")
