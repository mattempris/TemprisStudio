import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Check, ChevronDown, Search, X } from "lucide-react";
import { cn } from "../../lib/cn";

/**
 * A multi-select filter: one button that opens a checkbox list.
 *
 * Replaces the wall of chips this app used in two places. Fourteen chips across four rows is
 * ~140px of permanent vertical space above the thing being filtered, and in Work Design Studio
 * that pushed the panels a user is meant to work in below the fold. A closed dropdown is one
 * line; the options only take space while someone is choosing them.
 *
 * The count stays on every row, because it is what makes a choice informed rather than a guess
 * — the chips carried it and losing it would be a regression, not a simplification. `total` on
 * the trigger does the same job for the collapsed state.
 *
 * Checkboxes, and the panel deliberately stays open while they are ticked: these filters are
 * routinely used two or three values at a time, and a panel that closed on the first click
 * would cost a reopen per value. Escape and an outside click close it; Enter and Space toggle
 * a row, so the whole control works from the keyboard.
 *
 * "Nothing selected means everything" is the convention across the app, so the trigger reads
 * "All" rather than "None" when the selection is empty.
 */

export interface DropdownOption<T extends string | number> {
  value: T;
  label: string;
  /** Shown right-aligned on the row — how much of the data this option covers. */
  count?: number;
}

export function CheckboxDropdown<T extends string | number>({
  label,
  options,
  selected,
  onChange,
  hint,
  /** Show a filter box once the list is longer than this. */
  searchAfter = 10,
  className,
}: {
  label: string;
  options: DropdownOption<T>[];
  selected: T[];
  onChange: (next: T[]) => void;
  hint?: string;
  searchAfter?: number;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const box = useRef<HTMLDivElement>(null);
  const listId = useId();

  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setOpen(false);
        // Focus goes back to the trigger, or Escape would drop the user out of the toolbar.
        box.current?.querySelector<HTMLButtonElement>("button")?.focus();
      }
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  // The query is per-opening, not persistent: a filter box still holding last time's text is a
  // list that looks mysteriously short.
  useEffect(() => {
    if (!open) setQuery("");
  }, [open]);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? options.filter((o) => o.label.toLowerCase().includes(q)) : options;
  }, [options, query]);

  if (!options.length) return null;

  const summary =
    selected.length === 0
      ? "All"
      : selected.length === 1
        ? (options.find((o) => o.value === selected[0])?.label ?? "1 selected")
        : `${selected.length} selected`;

  function toggle(v: T) {
    onChange(selected.includes(v) ? selected.filter((x) => x !== v) : [...selected, v]);
  }

  return (
    <div ref={box} className={cn("relative min-w-0", className)}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        title={hint}
        className={cn(
          "flex w-full items-center gap-1.5 rounded-[8px] border px-2 py-1 text-left transition-colors",
          selected.length
            ? "border-accent bg-accent-bg"
            : "border-border bg-card hover:border-accent",
        )}
      >
        <span className="min-w-0 flex-1">
          <span className="block text-[9.5px] font-extrabold uppercase tracking-wider text-text-muted">
            {label}
          </span>
          <span
            className={cn(
              "block truncate text-[11.5px] font-semibold",
              selected.length ? "text-accent" : "text-text-secondary",
            )}
          >
            {summary}
          </span>
        </span>
        {selected.length > 0 && (
          // A span, not a button: a button inside a button is invalid HTML and the browser
          // will hoist it out of the trigger.
          <span
            role="button"
            tabIndex={0}
            aria-label={`Clear ${label}`}
            onClick={(e) => {
              e.stopPropagation();
              onChange([]);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                e.stopPropagation();
                onChange([]);
              }
            }}
            className="shrink-0 rounded-[4px] p-0.5 text-accent hover:bg-card"
          >
            <X size={11} />
          </span>
        )}
        <ChevronDown
          size={12}
          className={cn("shrink-0 text-text-muted transition-transform", open && "rotate-180")}
        />
      </button>

      {open && (
        <div
          id={listId}
          // Sizes to its content rather than to the trigger, capped so a long option cannot
          // stretch it off screen. A 190px trigger would otherwise truncate most of these
          // labels to "Risk, Compliance & Financial C…", and the label is the whole point.
          className="absolute left-0 top-[calc(100%+3px)] z-40 w-max min-w-full max-w-[min(360px,88vw)] rounded-[10px] border border-border bg-card shadow-modal"
        >
          {options.length > searchAfter && (
            <div className="flex items-center gap-1.5 border-b border-border px-2 py-1.5">
              <Search size={11} className="shrink-0 text-text-muted" />
              <input
                autoFocus
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={`Filter ${label.toLowerCase()}…`}
                className="min-w-0 flex-1 bg-transparent text-[11.5px] text-text outline-none placeholder:text-text-muted"
              />
            </div>
          )}

          <div className="max-h-[280px] overflow-y-auto py-1">
            {shown.length === 0 && (
              <p className="px-2.5 py-2 text-[11px] text-text-muted">Nothing matches that.</p>
            )}
            {shown.map((o) => {
              const on = selected.includes(o.value);
              return (
                <button
                  key={String(o.value)}
                  type="button"
                  role="option"
                  aria-selected={on}
                  onClick={() => toggle(o.value)}
                  className="flex w-full items-center gap-2 px-2 py-1 text-left hover:bg-panel"
                >
                  <span
                    className={cn(
                      "flex h-3 w-3 shrink-0 items-center justify-center rounded-[3px] border",
                      on ? "border-accent bg-accent text-white" : "border-border bg-card",
                    )}
                  >
                    {on && <Check size={9} strokeWidth={3.5} />}
                  </span>
                  <span
                    className={cn(
                      "min-w-0 flex-1 truncate text-[11.5px]",
                      on ? "font-semibold text-text" : "text-text-secondary",
                    )}
                  >
                    {o.label}
                  </span>
                  {o.count !== undefined && (
                    <span className="shrink-0 text-[10.5px] tabular-nums text-text-muted">
                      {o.count.toLocaleString()}
                    </span>
                  )}
                </button>
              );
            })}
          </div>

          <div className="flex items-center justify-between gap-2 border-t border-border px-2 py-1">
            <button
              type="button"
              onClick={() => onChange([])}
              disabled={selected.length === 0}
              className="text-[10.5px] font-semibold text-accent hover:underline disabled:text-text-muted disabled:no-underline"
            >
              All {label.toLowerCase()}
            </button>
            <span className="text-[10.5px] tabular-nums text-text-muted">
              {selected.length}/{options.length}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
