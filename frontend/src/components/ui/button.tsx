import type { ComponentProps } from "react"
import { cn } from "@/lib/utils"

export function Button({ className, type = "button", ...props }: ComponentProps<"button">) {
  return (
    <button
      type={type}
      data-slot="button"
      className={cn(
        "inline-flex min-h-11 cursor-pointer items-center justify-center gap-2 rounded-md",
        "bg-primary px-4 text-sm font-semibold text-primary-foreground hover:bg-primary-hover",
        "disabled:cursor-default disabled:opacity-60",
        className,
      )}
      {...props}
    />
  )
}
