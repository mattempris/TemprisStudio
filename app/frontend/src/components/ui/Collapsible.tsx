import { useState, type ReactNode } from "react";
import { ChevronRight } from "lucide-react";
import { cn } from "../../lib/cn";

/**
 * Disclosure for configuration that must be reachable but should not dominate a
 * stage. Content is mounted only while open so the heavier editors do not fetch
 * or render until asked.
 */
export function Collapsible({
  title,
  subtitle,
  badge,
  children,
  defaultOpen = false,
}: {
  title: string;
  subtitle?: string;
  badge?: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="overflow-hidden rounded-[10px] border border-border bg-panel">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3.5 py-2.5 text-left transition-colors hover:bg-card"
      >
        <ChevronRight
          size={13}
          className={cn("shrink-0 text-text-muted transition-transform", open && "rotate-90")}
        />
        <span className="min-w-0 flex-1">
          <span className="block text-[12.5px] font-semibold text-text">{title}</span>
          {subtitle && (
            <span className="mt-0.5 block text-[11.5px] leading-snug text-text-secondary">
              {subtitle}
            </span>
          )}
        </span>
        {badge}
      </button>
      {open && <div className="border-t border-border bg-card px-3.5 py-3">{children}</div>}
    </div>
  );
}
