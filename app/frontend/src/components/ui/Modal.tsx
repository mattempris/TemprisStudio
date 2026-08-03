import { useEffect, type ReactNode } from "react";
import { X } from "lucide-react";

/**
 * Centred dialog with a scrolling body and a fixed header.
 *
 * The body scrolls rather than the page: these open over a step that is itself
 * thousands of pixels tall, and scrolling the page underneath would lose the
 * user's place in it.
 */
export function Modal({
  title,
  subtitle,
  onClose,
  children,
  footer,
}: {
  title: string;
  subtitle?: ReactNode;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
}) {
  // Escape closes, and the page behind is frozen while it is open so a scroll
  // gesture that overshoots the list does not silently move the page.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prev;
      window.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-[var(--color-overlay)] p-6"
      onClick={onClose}
    >
      <div
        // Stops a click inside the panel reaching the backdrop's close handler.
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="flex max-h-[80vh] w-full max-w-lg flex-col overflow-hidden rounded-[20px] border border-border bg-card shadow-[var(--shadow-modal)]"
      >
        <div className="flex shrink-0 items-start gap-3 border-b border-border px-5 py-3.5">
          <div className="min-w-0 flex-1">
            <p className="truncate text-[13.5px] font-bold text-text">{title}</p>
            {subtitle && (
              <p className="mt-0.5 text-[11.5px] leading-snug text-text-secondary">{subtitle}</p>
            )}
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            className="shrink-0 rounded-[6px] p-1 text-text-muted transition-colors hover:bg-panel hover:text-text"
          >
            <X size={15} />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-3">{children}</div>

        {footer && (
          <div className="shrink-0 border-t border-border bg-panel px-5 py-2.5">{footer}</div>
        )}
      </div>
    </div>
  );
}
