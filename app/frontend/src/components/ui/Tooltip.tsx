import { useCallback, useRef, useState, type ReactNode } from "react";

/**
 * Hover/focus tooltip, following the style guide's tooltip pattern.
 *
 * Positioned `fixed` from the trigger's own rect rather than absolutely inside it.
 * That is deliberate: the things this is used on (cluster rows, size chips) sit
 * inside `overflow-hidden` containers, which clip an absolutely-positioned panel
 * to a sliver. Fixed positioning escapes the clip without needing a portal.
 *
 * Flips horizontally near the right edge and vertically near the bottom, so a
 * cluster at the end of a wrapped chip row or the last row of a long list still
 * shows its full contents.
 */

interface Props {
  content: ReactNode;
  children: ReactNode;
  className?: string;
  /** Max panel width in px. */
  width?: number;
}

export function Tooltip({ content, children, className, width = 280 }: Props) {
  const [pos, setPos] = useState<{ left: number; top: number; above: boolean } | null>(null);
  const ref = useRef<HTMLSpanElement>(null);

  const show = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const flipX = r.left + width + 16 > window.innerWidth;
    // 240 deliberately over-estimates the panel height: measuring it would need a
    // render pass first, and over-estimating only flips a little early.
    const above = r.bottom + 240 > window.innerHeight && r.top > 240;
    setPos({
      left: flipX ? Math.max(8, r.right - width) : r.left,
      top: above ? r.top - 6 : r.bottom + 6,
      above,
    });
  }, [width]);

  const hide = useCallback(() => setPos(null), []);

  return (
    <span
      ref={ref}
      className={className}
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={hide}
      tabIndex={0}
    >
      {children}
      {pos && (
        <span
          role="tooltip"
          style={{
            left: pos.left,
            top: pos.top,
            width,
            // When flipped, `top` is the trigger's top edge, so the panel has to be
            // pulled up by its own height to sit above rather than over it.
            transform: pos.above ? "translateY(-100%)" : undefined,
          }}
          className="pointer-events-none fixed z-50 block rounded-[10px] border border-border bg-card px-3 py-2 text-left shadow-[0_12px_32px_rgba(0,0,0,0.18)]"
        >
          {content}
        </span>
      )}
    </span>
  );
}

/** The shared body for "what is actually in this cluster" tooltips.
 *
 *  Two labelled counts, because above the profile tier they differ and conflating
 *  them misreads: a job family holds (say) 4 categories but 260 source job records.
 *  `heading` names what the cluster contains directly; `subheading` names what the
 *  listed titles are. */
export function TitleListTooltip({
  heading,
  subheading,
  titles,
  total,
  omitted = 0,
  clickForDetails = false,
}: {
  heading: string;
  subheading: string;
  titles: string[];
  /** Every source record beneath the cluster, which exceeds `titles.length` both
   *  because the list is capped and because repeats are collapsed to "×N". */
  total: number;
  /** Distinct titles the sample left out. */
  omitted?: number;
  /** Set where clicking the tile opens the detail dialog. The hover list collapses
   *  repeats and has no room for prose, so two tiles holding differently-described
   *  records with the same name look identical here — the note is what tells you
   *  there is more to see rather than leaving that as an apparent bug. */
  clickForDetails?: boolean;
}) {
  return (
    <>
      <span className="block text-[10px] font-extrabold uppercase tracking-wider text-text-muted">
        {heading}
      </span>
      <span className="mb-1 block border-b border-border pb-1 text-[10.5px] text-text-secondary">
        {subheading} <span className="font-bold tabular-nums text-text">{total}</span>
      </span>
      {titles.length === 0 ? (
        <span className="block text-[11.5px] text-text-muted">No source titles resolved.</span>
      ) : (
        <>
          <span className="block">
            {titles.map((t, i) => (
              <span key={`${t}-${i}`} className="block truncate text-[11.5px] leading-snug text-text">
                {t}
              </span>
            ))}
          </span>
          {omitted > 0 && (
            <span className="mt-1 block text-[10.5px] text-text-muted">
              +{omitted} more distinct {omitted === 1 ? "title" : "titles"}
            </span>
          )}
        </>
      )}
      {clickForDetails && (
        <span className="mt-1.5 block border-t border-border pt-1 text-[10px] font-semibold text-accent">
          Click cluster tile for details
        </span>
      )}
    </>
  );
}
