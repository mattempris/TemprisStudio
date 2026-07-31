import type { HTMLAttributes } from "react";
import { cn } from "../../lib/cn";

type BadgeColor = "brand" | "accent" | "success" | "warning" | "teal" | "purple" | "orange";

interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  color?: BadgeColor;
}

// {color}-bg / {color}-border pattern lifted directly from style guide.html's
// badge/status-pill CSS (e.g. .oc-newtag, .bkDone).
export function Badge({ color = "accent", className, ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-block rounded-full border px-2 py-0.5 text-[9px] font-extrabold uppercase tracking-wider",
        color === "brand" && "bg-brand-bg border-brand-border text-brand",
        color === "accent" && "bg-accent-bg border-accent-border text-accent",
        color === "success" && "bg-success-bg border-success-border text-success",
        color === "warning" && "bg-warning-bg border-warning-border text-warning",
        color === "teal" && "bg-teal-bg border-teal-border text-teal",
        color === "purple" && "bg-purple-bg border-purple-border text-purple",
        color === "orange" && "bg-orange-bg border-orange-border text-orange",
        className,
      )}
      {...props}
    />
  );
}
