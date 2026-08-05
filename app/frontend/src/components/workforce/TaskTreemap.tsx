import { useMemo, useState } from "react";
import type { ClusterOpportunity, RoleTask } from "../../types/workforce";
import { OPPORTUNITY_CEILING, opportunityColor } from "../../lib/heat";

/**
 * How a role spends its week, as a treemap — the Task Map pattern from the insurance
 * demo, rebuilt on this app's data and colour scale.
 *
 * A ranked list of tasks with a percentage column answers "which task is most
 * automatable" and hides the thing a client actually reacts to: the *shape* of the week.
 * A role with one task at 40% and eight at 7.5% reads identically to a role with nine
 * even tasks when both are twelve rows of text. Sized cells make that instant, and put
 * the automation score where it belongs — on the area it applies to, so a hot sliver
 * cannot be mistaken for a big win.
 *
 * Squarified layout (Bruls/Huizing/van Wijk) computed here rather than pulled in as a
 * dependency; it is thirty lines and the alternative is a charting library for one chart.
 * Aspect ratio matters: naive slice-and-dice gives long thin slivers whose area the eye
 * cannot compare, which defeats the purpose of using area to encode share.
 *
 * The actions live in the tooltip. They are the level opportunity is real at — "Handling
 * Customer Complaints" is not automatable or not, the acknowledgement letter inside it
 * largely is — but there are four or five per task, so putting them inline turns the
 * role back into the wall of rows this replaces.
 */

type Metric = "automation" | "augmentation";

interface Cell {
  x: number;
  y: number;
  w: number;
  h: number;
  task: RoleTask;
}

/** Layout `items` (value + payload) into the rectangle, appending to `out`. */
export function squarify(
  items: { v: number; task: RoleTask }[],
  x: number,
  y: number,
  w: number,
  h: number,
  out: Cell[],
): void {
  if (!items.length) return;
  if (items.length === 1) {
    out.push({ x, y, w, h, task: items[0].task });
    return;
  }
  const total = items.reduce((s, it) => s + it.v, 0);
  if (total <= 0) return;

  // Grow the current row while the worst aspect ratio in it keeps improving.
  const short = Math.min(w, h);
  const long = Math.max(w, h);
  let best = 1;
  let bestRatio = Infinity;
  for (let k = 1; k <= items.length; k++) {
    const rowSum = items.slice(0, k).reduce((s, it) => s + it.v, 0);
    const rowThick = (rowSum / total) * long;
    let worst = 0;
    for (const it of items.slice(0, k)) {
      const len = (it.v / rowSum) * short;
      if (len <= 0 || rowThick <= 0) continue;
      worst = Math.max(worst, Math.max(rowThick / len, len / rowThick));
    }
    if (worst <= bestRatio) {
      bestRatio = worst;
      best = k;
    } else break;
  }

  const row = items.slice(0, best);
  const rest = items.slice(best);
  const rowSum = row.reduce((s, it) => s + it.v, 0);
  if (w >= h) {
    const rw = (rowSum / total) * w;
    let cy = y;
    for (const it of row) {
      const ch = (it.v / rowSum) * h;
      out.push({ x, y: cy, w: rw, h: ch, task: it.task });
      cy += ch;
    }
    squarify(rest, x + rw, y, w - rw, h, out);
  } else {
    const rh = (rowSum / total) * h;
    let cx = x;
    for (const it of row) {
      const cw = (it.v / rowSum) * w;
      out.push({ x: cx, y, w: cw, h: rh, task: it.task });
      cx += cw;
    }
    squarify(rest, x, y + rh, w, h - rh, out);
  }
}

const WIDTH = 1000; // layout units across; converted to % so the map is fluid
const HEIGHT = 230; // px, the one fixed dimension

// The box is the whole week. That is only true because task inference constrains each
// role's proportions to sum to 100 (checked: all 565 roles on the reference project sum
// to exactly 100), so the layout's normalisation by the item sum and normalisation by
// 100 are the same operation. Unassessed tasks therefore keep their area rather than
// being dropped — see scoreOf below.

export function TaskTreemap({
  tasks,
  actionsByCluster,
  heat,
}: {
  tasks: RoleTask[];
  actionsByCluster: Map<number, ClusterOpportunity["actions"]>;
  heat: boolean;
}) {
  const [metric, setMetric] = useState<Metric>("automation");
  const [hover, setHover] = useState<{ cell: Cell; x: number; y: number } | null>(null);

  const cells = useMemo(() => {
    const items = tasks
      .filter((t) => t.proportion > 0)
      .map((t) => ({ v: t.proportion, task: t }))
      .sort((a, b) => b.v - a.v);
    const out: Cell[] = [];
    squarify(items, 0, 0, WIDTH, HEIGHT, out);
    return out;
  }, [tasks]);

  // Tasks with no assessed cluster have no score. They still take up the person's week,
  // so they keep their area and are shown hatched rather than dropped — a treemap that
  // silently omitted them would overstate how much of the role has been assessed.
  const scoreOf = (t: RoleTask) => (metric === "automation" ? t.automation : t.augmentation);

  if (!cells.length) {
    return (
      <p className="px-3 py-6 text-center text-[12px] text-text-muted">
        No task proportions recorded for this role.
      </p>
    );
  }

  return (
    <div>
      <div className="mb-1.5 flex flex-wrap items-center justify-between gap-2">
        <p className="text-[10.5px] text-text-muted">
          Sized by share of the role's week
          {heat && `, shaded by ${metric === "automation" ? "automation" : "augmentation"} potential`}
          {" · hover a cell for its actions"}
        </p>
        {heat && (
          <span className="flex rounded-[6px] border border-border bg-card p-0.5">
            {(["automation", "augmentation"] as Metric[]).map((m) => (
              <button
                key={m}
                onClick={() => setMetric(m)}
                className={`rounded-[4px] px-2 py-0.5 text-[10.5px] font-semibold transition-colors ${
                  metric === m ? "bg-accent-bg text-accent" : "text-text-secondary hover:text-text"
                }`}
              >
                {m === "automation" ? "Automate" : "Augment"}
              </button>
            ))}
          </span>
        )}
      </div>

      <div
        className="relative w-full overflow-hidden rounded-[8px] border border-border"
        style={{ height: HEIGHT }}
        onMouseLeave={() => setHover(null)}
      >
        {cells.map((c, i) => {
          const score = scoreOf(c.task);
          const shaded = heat && score !== null;
          // White numerals need a dark enough cell; the ramp darkens with the score, so
          // the crossover is a score threshold rather than a per-colour calculation.
          const light = shaded && score >= OPPORTUNITY_CEILING * 0.35;
          return (
            <div
              key={`${c.task.cluster_id}-${i}`}
              onMouseEnter={(e) =>
                setHover({ cell: c, x: e.clientX, y: e.clientY })
              }
              onMouseMove={(e) => setHover({ cell: c, x: e.clientX, y: e.clientY })}
              className="absolute flex flex-col justify-center overflow-hidden px-1.5 py-1 transition-[outline] hover:outline hover:outline-2 hover:-outline-offset-2 hover:outline-accent"
              style={{
                left: `${(c.x / WIDTH) * 100}%`,
                top: c.y,
                width: `${(c.w / WIDTH) * 100}%`,
                height: c.h,
                background: shaded ? opportunityColor(score) : "var(--color-panel)",
                color: light ? "#fff" : "var(--color-text)",
                boxShadow: "inset 0 0 0 1px var(--color-card)",
                // An unassessed task is visibly unassessed rather than looking like a
                // zero score, which would read as "no opportunity here".
                backgroundImage:
                  score === null
                    ? "repeating-linear-gradient(45deg, transparent, transparent 4px, rgba(0,0,0,.05) 4px, rgba(0,0,0,.05) 8px)"
                    : undefined,
              }}
            >
              {/* Thresholds in layout units: below these the text is unreadable and
                  clipping it mid-word looks like a rendering fault. */}
              {c.w >= 95 && c.h >= 30 && (
                <span className="truncate text-[10.5px] font-semibold leading-tight">
                  {c.task.name}
                </span>
              )}
              {c.w >= 48 && c.h >= 20 && (
                <span className="text-[10px] tabular-nums leading-tight opacity-80">
                  {c.task.proportion.toFixed(1)}%
                  {heat && score !== null && ` · ${Math.round(score)}%`}
                </span>
              )}
            </div>
          );
        })}
      </div>

      {hover && (
        <TreemapTooltip
          cell={hover.cell}
          actions={actionsByCluster.get(hover.cell.task.cluster_id) ?? []}
          x={hover.x}
          y={hover.y}
        />
      )}
    </div>
  );
}

/**
 * Fixed to the viewport rather than the cell, and flipped near an edge.
 *
 * The map sits inside a scrolling panel and the tooltip is taller than most cells, so
 * anchoring it to the cell put it off-screen for anything in the lower half.
 */
function TreemapTooltip({
  cell,
  actions,
  x,
  y,
}: {
  cell: Cell;
  actions: ClusterOpportunity["actions"];
  x: number;
  y: number;
}) {
  const W = 340;
  const H = Math.min(340, 116 + actions.length * 30);
  const pad = 14;
  const left = x + pad + W > window.innerWidth - 8 ? Math.max(8, x - W - pad) : x + pad;
  const top = y + pad + H > window.innerHeight - 8 ? Math.max(8, y - H - pad) : y + pad;
  const t = cell.task;

  return (
    <div
      className="pointer-events-none fixed z-50 rounded-[8px] border border-border bg-card px-3 py-2 shadow-[var(--shadow-elevated)]"
      style={{ left, top, width: W }}
    >
      <p className="text-[11.5px] font-bold leading-snug text-text">{t.name}</p>
      <p className="mt-0.5 text-[10.5px] leading-snug text-text-muted">{t.cluster}</p>
      <p className="mt-1 border-t border-border pt-1 text-[10.5px] text-text-secondary">
        <strong className="text-text">{t.proportion.toFixed(1)}%</strong> of the role
        {t.automation !== null && (
          <>
            {" · "}
            <strong className="text-text">{Math.round(t.automation)}%</strong> automatable
          </>
        )}
        {t.augmentation !== null && (
          <>
            {" · "}
            <strong className="text-text">{Math.round(t.augmentation)}%</strong> augmentable
          </>
        )}
      </p>
      {actions.length === 0 ? (
        <p className="mt-1.5 text-[10.5px] text-text-muted">
          {t.automation === null
            ? "This task's cluster has not been assessed."
            : "No action breakdown recorded for this cluster."}
        </p>
      ) : (
        <>
          <div className="mt-1.5 flex items-center gap-2 border-b border-border pb-0.5 text-[9.5px] font-extrabold uppercase tracking-wider text-text-muted">
            <span className="min-w-0 flex-1">Action</span>
            <span className="w-10 text-right">% task</span>
            <span className="w-9 text-right">Auto</span>
            <span className="w-9 text-right">Augm</span>
          </div>
          {actions.map((a) => (
            <div
              key={a.name}
              className="flex items-baseline gap-2 border-b border-border/50 py-0.5 last:border-0 text-[10.5px]"
            >
              <span className="min-w-0 flex-1 truncate text-text">{a.name}</span>
              <span className="w-10 shrink-0 text-right tabular-nums text-text-secondary">
                {a.pct_of_task.toFixed(0)}
              </span>
              <span className="w-9 shrink-0 text-right tabular-nums text-text-secondary">
                {Math.round(a.automation)}
              </span>
              <span className="w-9 shrink-0 text-right tabular-nums text-text-secondary">
                {Math.round(a.augmentation)}
              </span>
            </div>
          ))}
        </>
      )}
    </div>
  );
}
