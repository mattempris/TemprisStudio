import { useMemo, useState } from "react";
import { useDraggable, useDroppable } from "@dnd-kit/core";
import { LayoutGrid, List, Plus } from "lucide-react";
import { Treemap, type TreemapCell, type TreemapDatum } from "../workforce/Treemap";
import type { PoolCluster, PoolResult } from "../../types/workDesign";

/**
 * The unreviewed work — the pool a designed job draws from, and which drains as it is drawn.
 *
 * This is not a description of what people do today; it is a budget of work to be re-allocated.
 * So a cluster's tile shrinks as levers absorb it and as it is assigned to job definitions, and
 * "finished" means the pool is empty.
 *
 * Cells are draggable, but **the click path is the primary one**: a `+` on every cell and on
 * every list row does exactly what a drop does. Cells are sized by data, so the tail of a
 * 90-cluster aggregate is a few pixels across and impossible to aim at — and drag needs a
 * keyboard and touch equivalent regardless. Building the buttons first means the studio is
 * complete without drag, and drag is an accelerant rather than the only way in.
 */

const HEIGHT = 320;
// Below about 1.2% of the box a cell cannot hold a label or be aimed at, and a job family rolls
// up to 60-90 clusters of which most are under 1%.
const MIN_SHARE = 0.012;

export function PoolPanel({
  pool,
  unit,
  onAdd,
  dropDisabled,
}: {
  pool: PoolResult;
  unit: string;
  onAdd: (cluster: PoolCluster, hours: number) => void;
  dropDisabled?: boolean;
}) {
  const [view, setView] = useState<"map" | "list">("map");
  // Dropping a designed-job line back here removes it — the reverse of taking work out.
  const { setNodeRef, isOver } = useDroppable({ id: "pool", disabled: dropDisabled });

  const data = useMemo<TreemapDatum<PoolCluster>[]>(
    () =>
      pool.clusters.map((c) => ({
        id: `pool-${c.cluster_id}`,
        value: c.hours_per_week,
        label: c.name,
        // Hours, not percentages, in both panels. Hours survive the two panels normalising to
        // different totals; percentages do not, and comparing them across panels would be a lie.
        sub: `${c.hours_per_week.toFixed(0)} h`,
        score: c.automation,
        payload: c,
      })),
    [pool.clusters],
  );

  const s = pool.sample;
  const t = pool.totals;

  return (
    <div
      ref={setNodeRef}
      className={`rounded-[var(--radius-modal)] border bg-card shadow-modal transition-colors ${
        isOver ? "border-accent ring-2 ring-accent" : "border-border"
      }`}
    >
      <div className="flex flex-wrap items-start justify-between gap-2 border-b border-border px-4 py-3">
        <div className="min-w-0">
          <p className="text-[13px] font-bold text-text">Unreviewed work</p>
          <p className="mt-0.5 text-[11px] leading-snug text-text-muted">
            {s ? `${s.job_profiles} job profiles · ${s.headcount.toFixed(0)} ${unit}` : ""}
            {" · "}
            <strong className="text-text-secondary">
              {t.remaining_hours_per_week.toLocaleString(undefined, { maximumFractionDigits: 0 })} h
              a week
            </strong>{" "}
            still to allocate
          </p>
        </div>
        <span className="flex shrink-0 rounded-[6px] border border-border bg-card p-0.5">
          {(["map", "list"] as const).map((v) => (
            <button
              key={v}
              onClick={() => setView(v)}
              title={v === "map" ? "Treemap" : "List — the keyboard-friendly view"}
              className={`rounded-[4px] px-1.5 py-0.5 transition-colors ${
                view === v ? "bg-accent-bg text-accent" : "text-text-secondary hover:text-text"
              }`}
            >
              {v === "map" ? <LayoutGrid size={11} /> : <List size={11} />}
            </button>
          ))}
        </span>
      </div>

      <div className="px-4 py-3">
        {pool.clusters.length === 0 ? (
          <p className="py-10 text-center text-[12px] text-text-muted">
            {t.as_is_hours_per_week > 0
              ? "Every hour of this slice is either absorbed by a lever or allocated to a job. The pool is empty."
              : "No work matches this filter."}
          </p>
        ) : view === "map" ? (
          <Treemap
            data={data}
            height={HEIGHT}
            minCellShare={MIN_SHARE}
            renderCell={(c) => <PoolCell cell={c} onAdd={onAdd} />}
            cellProps={(c) => ({ "data-cluster": c.datum.payload.cluster_id })}
          />
        ) : (
          <div className="max-h-[320px] overflow-y-auto">
            {pool.clusters.map((c) => (
              <div
                key={c.cluster_id}
                className="flex items-center gap-2 border-b border-border/60 py-1 last:border-0"
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[11.5px] text-text">{c.name}</span>
                  <span className="block truncate text-[10.5px] text-text-muted">
                    {c.domain} · {c.n_roles} roles
                    {c.assessed ? ` · ${c.automation?.toFixed(0)}% automatable` : " · not assessed"}
                  </span>
                </span>
                <span className="shrink-0 text-right text-[11.5px] tabular-nums text-text-secondary">
                  {c.hours_per_week.toFixed(0)} h
                </span>
                <button
                  onClick={() => onAdd(c, c.hours_per_holder_week)}
                  title="Add one holder's share to the job design"
                  className="shrink-0 rounded-[6px] border border-border px-1.5 py-0.5 text-[10.5px] font-semibold text-accent hover:border-accent"
                >
                  <Plus size={10} />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {pool.warnings.length > 0 && (
        <div className="border-t border-border px-4 py-2">
          {pool.warnings.map((w) => (
            <p key={w} className="text-[10.5px] leading-snug text-warning">
              {w}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}

/** A draggable pool cell. Its position is the data, so the drag overlay does the moving. */
function PoolCell({
  cell,
  onAdd,
}: {
  cell: TreemapCell<PoolCluster>;
  onAdd: (cluster: PoolCluster, hours: number) => void;
}) {
  const c = cell.datum.payload;
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: `pool-${c.cluster_id}`,
    data: { from: "pool", cluster: c },
  });
  const big = cell.w >= 95 && cell.h >= 30;

  return (
    <div
      ref={setNodeRef}
      {...listeners}
      {...attributes}
      // Enter and Space do exactly what a drop does. This is the accessible path, not a
      // fallback — keyboard drag across two panels gives a user nothing to navigate by.
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onAdd(c, c.hours_per_holder_week);
        }
      }}
      tabIndex={0}
      role="button"
      aria-label={`${c.name}. ${c.hours_per_week.toFixed(0)} hours a week across the sample, ${c.hours_per_holder_week.toFixed(1)} per holder${
        c.assessed ? `, ${c.automation?.toFixed(0)}% automatable` : ", not assessed"
      }. Press Enter to add to the job design.`}
      className="flex h-full w-full cursor-grab flex-col justify-center outline-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
      style={{ opacity: isDragging ? 0.35 : 1 }}
    >
      {big && (
        <span className="truncate text-[10.5px] font-semibold leading-tight">{cell.datum.label}</span>
      )}
      {cell.w >= 48 && cell.h >= 20 && (
        <span className="truncate text-[10px] tabular-nums leading-tight opacity-80">
          {cell.datum.sub}
        </span>
      )}
      {big && (
        <button
          onClick={(e) => {
            e.stopPropagation();
            onAdd(c, c.hours_per_holder_week);
          }}
          title={`Add ${c.hours_per_holder_week.toFixed(1)} h — one holder's share`}
          className="absolute right-1 top-1 rounded-[4px] bg-card/80 px-1 text-[10px] font-bold text-accent hover:bg-card"
        >
          +
        </button>
      )}
    </div>
  );
}
