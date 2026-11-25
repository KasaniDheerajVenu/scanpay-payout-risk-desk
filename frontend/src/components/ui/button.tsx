import type { ComponentProps } from "react"
import { cn } from "@/lib/utils"

export function Button({ className, type = "button", variant = "primary", ...props }:
ComponentProps<"button"> & { variant?: "primary" | "secondary" | "ghost" }) {
  return (
    <button
      type={type}
      data-slot="button"
      className={cn(
        "inline-flex min-h-11 cursor-pointer items-center justify-center gap-2 rounded-md",
        "px-4 text-sm font-semibold",
        variant === "primary" && "bg-primary text-primary-foreground hover:bg-primary-hover",
        variant === "secondary" && "border border-border bg-card text-foreground hover:bg-muted",
        variant === "ghost" && "text-foreground hover:bg-muted",
        "disabled:cursor-default disabled:opacity-60",
        className,
      )}
      {...props}
    />
  )
}
