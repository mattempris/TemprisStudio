import { useEffect, useRef, useState } from "react";
import { Palette } from "lucide-react";
import { PALETTES, applyStoredPalette, setPalette, type PaletteId } from "../../lib/palette";

/** Palette picker. Definitions and persistence live in lib/palette.ts. */
export function PaletteSwitcher() {
  const [current, setCurrent] = useState<PaletteId>(() => applyStoredPalette());
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    setPalette(current);
  }, [current]);

  // Click-away. Bound on pointerdown against a containment check rather than a
  // blanket window click: a click handler registered while the opening click is
  // still propagating closes the popover before it paints.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("pointerdown", onDown);
    return () => window.removeEventListener("pointerdown", onDown);
  }, [open]);

  const active = PALETTES.find((p) => p.id === current) ?? PALETTES[0];

  return (
    <span ref={box} className="relative inline-block">
      <button
        onClick={() => setOpen((v) => !v)}
        title={`Palette: ${active.name}`}
        aria-label="Change palette"
        className="flex items-center gap-1.5 rounded-[7px] border border-border bg-card px-2 py-1 text-[11.5px] font-semibold text-text-secondary transition-colors hover:bg-panel hover:text-text"
      >
        <Palette size={12} />
        <span className="hidden sm:inline">{active.name}</span>
      </button>

      {open && (
        <div className="absolute right-0 top-full z-50 mt-1 w-44 overflow-hidden rounded-[10px] border border-border bg-card shadow-[var(--shadow-modal)]">
          {PALETTES.map((p) => (
            <button
              key={p.id}
              onClick={() => {
                setCurrent(p.id);
                setOpen(false);
              }}
              className={`flex w-full items-center gap-2 px-3 py-2 text-left text-[12px] transition-colors hover:bg-panel ${
                p.id === current ? "font-bold text-accent" : "text-text"
              }`}
            >
              <span className="flex shrink-0 gap-0.5">
                {p.swatch.map((c) => (
                  <span
                    key={c}
                    style={{ background: c }}
                    className="h-3 w-3 rounded-[2px] border border-border"
                  />
                ))}
              </span>
              {p.name}
            </button>
          ))}
        </div>
      )}
    </span>
  );
}
