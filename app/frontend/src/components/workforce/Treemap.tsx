import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { layout, type Rect } from "../../lib/treemap";
import { OPPORTUNITY_CEILING, opportunityColor } from "../../lib/heat";
import { cn } from "../../lib/cn";

/**
 * A treemap, generic over what each cell stands for.
 *
 * Owns the things every treemap in this app agrees on — pixel-space layout, heat fill, the
 * hatch for an unassessed cell, label thresholds, the tail pool — and nothing about what the
 * cells mean. Callers keep their own payload and supply per-cell props through `cellProps`,
 * which is how drag-and-drop attaches without this module importing a DnD library.
 *
 * That escape hatch follows the convention `Button`, `Card` and `Badge` already use: spread
 * caller props after the defaults and merge `className` through `cn`.
 */

export interface TreemapDatum<P = unknown> {
  /** Stable across relayouts. React key, and later the drag id. */
  id: string;
  /** Area weight, in whatever unit the caller is working in. */
  value: number;
  label: string;
  /** Second line, e.g. "4.2 h/wk". */
  sub?: string;
  /** 0..OPPORTUNITY_CEILING for heat. `null` renders hatched — unknown, not zero. */
  score: number | null;
  payload: P;
}

export interface TreemapCell<P> extends Rect {
  datum: TreemapDatum<P>;
  /** True when the fill is dark enough for white numerals. */
  light: boolean;
  /** Set on the synthetic tail cell: the data it stands in for. */
  pooled: TreemapDatum<P>[] | null;
}

/**
 * Cells below this share of the box are pooled into one "+N smaller" cell.
 *
 * Not a nicety. A job family rolls up to 60-90 task clusters of which most are under 1%, and
 * ninety cells in a 460px panel is a texture rather than a chart — individually unreadable,
 * unclickable, and impossible to aim a drag at. Pooling the tail fixes the legibility, the
 * target size and the label thresholds in one move.
 */
const DEFAULT_MIN_SHARE = 0;

export function Treemap<P>({
  data,
  height,
  heat = true,
  minCellShare = DEFAULT_MIN_SHARE,
  extent,
  renderCell,
  cellProps,
  emptyMessage = "Nothing to show.",
  className,
}: {
  data: TreemapDatum<P>[];
  height: number;
  heat?: boolean;
  minCellShare?: number;
  /** Lay out over this total instead of the data's sum — see `layout`. */
  extent?: number;
  renderCell?: (cell: TreemapCell<P>) => ReactNode;
  cellProps?: (cell: TreemapCell<P>) => Record<string, unknown> | undefined;
  emptyMessage?: string;
  className?: string;
}) {
  const box = useRef<HTMLDivElement>(null);
  // Layout needs real pixels, so the first pass has to wait for a measurement. See the note
  // in lib/treemap about why abstract units are not good enough.
  const [width, setWidth] = useState(0);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      setWidth(entry.contentRect.width);
    });
    ro.observe(el);
    setWidth(el.getBoundingClientRect().width);
    return () => ro.disconnect();
  }, []);

  const cells = useMemo<TreemapCell<P>[]>(() => {
    if (width <= 0) return [];
    const live = data.filter((d) => d.value > 0);
    const total = live.reduce((s, d) => s + d.value, 0);
    if (!total) return [];

    // Pool the tail before laying out, so the pooled cell competes for area on its combined
    // value rather than being appended afterwards.
    const cut = minCellShare * total;
    const big = cut > 0 ? live.filter((d) => d.value >= cut) : live;
    const small = cut > 0 ? live.filter((d) => d.value < cut) : [];
    const items: { v: number; datum: TreemapDatum<P>; pooled: TreemapDatum<P>[] | null }[] =
      big.map((d) => ({ v: d.value, datum: d, pooled: null }));
    if (small.length) {
      const pooledValue = small.reduce((s, d) => s + d.value, 0);
      items.push({
        v: pooledValue,
        pooled: small,
        datum: {
          id: "__tail__",
          value: pooledValue,
          label: `+${small.length} smaller`,
          score: null,
          payload: small[0].payload,
        },
      });
    }

    return layout(items, width, height, extent).map((c) => {
      const score = c.datum.score;
      const shaded = heat && score !== null && !c.pooled;
      return {
        x: c.x,
        y: c.y,
        w: c.w,
        h: c.h,
        datum: c.datum,
        // White numerals need a dark enough cell; the ramp darkens with the score, so the
        // crossover is a score threshold rather than a per-colour calculation.
        light: shaded && (score as number) >= OPPORTUNITY_CEILING * 0.35,
        pooled: c.pooled,
      };
    });
  }, [data, width, height, heat, minCellShare, extent]);

  return (
    <div
      ref={box}
      className={cn(
        "relative w-full overflow-hidden rounded-[8px] border border-border",
        className,
      )}
      style={{ height }}
    >
      {width > 0 && cells.length === 0 && (
        <p className="flex h-full items-center justify-center px-3 text-center text-[12px] text-text-muted">
          {emptyMessage}
        </p>
      )}
      {cells.map((c) => {
        const score = c.datum.score;
        const shaded = heat && score !== null && !c.pooled;
        // Destructured rather than deleted: `cellProps` may hand back a memoised object,
        // and mutating a caller's value to strip two keys would be a genuinely nasty bug to
        // find — the second render would arrive with className and style already gone.
        const {
          className: extraClass,
          style: extraStyle,
          ...extra
        } = (cellProps?.(c) ?? {}) as {
          className?: string;
          style?: CSSProperties;
          [k: string]: unknown;
        };
        return (
          <div
            key={c.datum.id}
            {...extra}
            className={cn(
              "absolute flex flex-col justify-center overflow-hidden px-1.5 py-1 transition-[outline] hover:outline hover:outline-2 hover:-outline-offset-2 hover:outline-accent",
              extraClass,
            )}
            style={{
              left: c.x,
              top: c.y,
              width: c.w,
              height: c.h,
              background: shaded
                ? opportunityColor(score as number)
                : c.pooled
                  ? "var(--color-canvas)"
                  : "var(--color-panel)",
              color: c.light ? "#fff" : "var(--color-text)",
              boxShadow: "inset 0 0 0 1px var(--color-card)",
              // An unassessed cell is visibly unassessed rather than looking like a zero
              // score, which would read as "no opportunity here".
              backgroundImage:
                score === null && !c.pooled
                  ? "repeating-linear-gradient(45deg, transparent, transparent 4px, rgba(0,0,0,.05) 4px, rgba(0,0,0,.05) 8px)"
                  : undefined,
              ...(extraStyle ?? {}),
            }}
          >
            {renderCell ? (
              renderCell(c)
            ) : (
              <>
                {/* Thresholds are pixels now that layout is in pixels. They were layout
                    units, i.e. ~9.5% and ~4.8% of the container, which at 460px admitted
                    44px cells whose label truncated to nothing and looked like a fault. */}
                {c.w >= 95 && c.h >= 30 && (
                  <span className="truncate text-[10.5px] font-semibold leading-tight">
                    {c.datum.label}
                  </span>
                )}
                {c.w >= 48 && c.h >= 20 && c.datum.sub && (
                  <span className="truncate text-[10px] tabular-nums leading-tight opacity-80">
                    {c.datum.sub}
                  </span>
                )}
              </>
            )}
          </div>
        );
      })}
    </div>
  );
}
