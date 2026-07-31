import type { HTMLAttributes } from "react";
import { cn } from "../../lib/cn";

// style guide.html's .sectionCard (large containers, radius-modal, shadow-modal)
// vs .outcomeCard/.callout (radius-card, shadow-card) — this is the smaller,
// nested-content variant; pass className="rounded-[var(--radius-modal)]
// shadow-modal" for the larger structural-container variant.
export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "rounded-[var(--radius-card)] border border-border bg-card p-5 shadow-card",
        className,
      )}
      {...props}
    />
  );
}
