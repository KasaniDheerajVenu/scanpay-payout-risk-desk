import type { FileType, ImportMode } from "@/services/workspace"

export const fileTypes: FileType[] = [
  "teams", "history_transactions", "history_payouts", "latest_transactions", "pending_payouts",
]
export const fileLabels: Record<FileType, string> = {
  teams: "Teams",
  history_transactions: "Historical transactions",
  history_payouts: "Historical payouts",
  latest_transactions: "Latest transactions",
  pending_payouts: "Pending payouts",
}
export const defaultFileLimit = 4 * 1024 * 1024
export const defaultTotalLimit = 16 * 1024 * 1024
export type UploadFiles = Partial<Record<FileType, File>>

export const requiredFiles = (mode: ImportMode) => mode === "INITIAL"
  ? fileTypes : fileTypes.slice(3)

export function fileProblem(file: File) {
  if (!file.name.toLowerCase().endsWith(".csv")) return "Choose a CSV file."
  if (file.size === 0) return "The selected file is empty."
  if (file.size > defaultFileLimit) return "This file exceeds 4 MB."
}

export function makeUpload(mode: ImportMode, cutoff: string, files: UploadFiles, assessment: boolean) {
  const data = new FormData()
  data.set("mode", mode)
  data.set("cutoff", cutoff)
  if (mode === "INITIAL" && assessment) data.set("assessment", "true")
  for (const type of fileTypes) {
    const file = files[type]
    if (file) data.set(type, file)
  }
  return data
}
