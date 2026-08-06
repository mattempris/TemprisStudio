import { useMemo, useState } from "react";
import { useDraggable, useDroppable } from "@dnd-kit/core";
import { ChevronRight, LayoutGrid, List, Plus } from "lucide-react";
import { Treemap, type TreemapCell, type TreemapDatum } from "../workforce/Treemap";
import { levelOf, rollup, type RollupNode, type RollupPath } from "../../lib/poolRollup";
import type { PoolCluster, PoolResult } from "../../types/workDesign";

/**
 * The unreviewed work — the pool a designed job draws from, and which drains as it is drawn.
 *
 * This is not a description of what people do today; it is a budget of work to be re-allocated.
 * So a tile shrinks as levers absorb it and as it is assigned to job definitions, and "finished"
 * means the pool is empty.
 *
 * **Rolled up to task domain, drilled on click.** Flat, an unfiltered pool is 750 clusters whose
 * largest is 1% of the box — a texture rather than a chart, and no amount of tuning the tile
 * pooling fixed that, because the distribution genuinely has no shape at that resolution. Rolled
 * up to the ~14 domains it reads immediately, and the detail is one click in rather than gone.
 * Domain → category → cluster, with a breadcrumb back out.
 *
 * Click drills; the `+` allocates. Two gestures on one tile, and the one that changes the design
 * is the explicit one — clicking a tile is how you look, and looking should never move hours into
 * a job. On a leaf there is nothing to drill into, so there a click adds.
 *
 * Cells are draggable, but **the click path is the primary one**: every cell and every list row
 * carries a `+` that does exactly what a drop does. Cells are sized by data, so the tail of an
 * aggregate is a few pixels across and impossible to aim at — and drag needs a keyboard and
 * touch equivalent regardless. Building the buttons first means the studio is complete without
 * drag, and drag is an accelerant rather than the only way in.
 */

const HEIGHT = 320;
// A count, not a share. The first version used a minimum share of the box and it failed on
// exactly the data it was written for: the largest of 750 clusters is 1.0% of an unfiltered pool,
// so any share-based floor pools nearly all of them. With the rollup this rarely binds — a domain
// level has ~14 nodes — but the deepest level can still be a long list.
const MAX_CELLS = 40;

export function PoolPanel({
  pool,
  unit,
  onAdd,
  dropDisabled,
  forceList,
}: {
  pool: PoolResult;
  unit: string;
  /** Called once per real cluster. A rolled-up node hands over every leaf beneath it. */
  onAdd: (cluster: PoolCluster, hours: number) => void;
  dropDisabled?: boolean;
  /** Narrow screen: pin the list and hide the toggle. A treemap does not survive being narrow. */
  forceList?: boolean;
}) {
  const [pref, setView] = useState<"map" | "list">("map");
  const view = forceList ? "list" : pref;
  const [path, setPath] = useState<RollupPath>({});
  // Dropping a designed-job line back here removes it — the reverse of taking work out.
  const { setNodeRef, isOver } = useDroppable({ id: "pool", disabled: dropDisabled });

  const nodes = useMemo(() => rollup(pool.clusters, path), [pool.clusters, path]);
  const level = levelOf(path);

  // A drilled-in path whose domain or category no longer exists — the filter moved, or the levers
  // emptied it — would render a blank panel with no way out. Offer the way out.
  const stranded = nodes.length === 0 && pool.clusters.length > 0;

  const data = useMemo<TreemapDatum<RollupNode>[]>(
    () =>
      nodes.map((n) => ({
        id: n.key,
        value: n.hours_per_week,
        label: n.name,
        // Hours, not percentages, in both panels. Hours survive the two panels normalising to
        // different totals; percentages do not, and comparing them across panels would be a lie.
        sub:
          n.level === "cluster"
            ? `${n.hours_per_week.toFixed(0)} h`
            : `${n.hours_per_week.toFixed(0)} h · ${n.leaves.length}`,
        score: n.automation,
        payload: n,
      })),
    [nodes],
  );

  const t = pool.totals;
  const s = pool.sample;

  function addNode(n: RollupNode) {
    // One line per real cluster, never a synthetic "domain" line: a designed job's lines carry a
    // task_cluster_id, and the profile document has to name the actual work.
    for (const c of n.leaves) onAdd(c, c.hours_per_holder_week);
  }

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
            {s ? `${s.job_profiles} anchor roles · ${s.headcount.toFixed(0)} ${unit}` : ""}
            {" · "}
            <strong className="text-text-secondary">
              {t.remaining_hours_per_week.toLocaleString(undefined, { maximumFractionDigits: 0 })} h
              a week
            </strong>{" "}
            still to allocate
          </p>
        </div>
        {!forceList && (
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
        )}
      </div>

      <Breadcrumb pool={pool} path={path} onPath={setPath} level={level} count={nodes.length} />

      <div className="px-4 py-3">
        {pool.clusters.length === 0 ? (
          <p className="py-10 text-center text-[12px] text-text-muted">
            {t.as_is_hours_per_week > 0
              ? "Every hour of this slice is either absorbed by a lever or allocated to a job. The pool is empty."
              : "No work matches this filter."}
          </p>
        ) : stranded ? (
          <div className="py-10 text-center">
            <p className="text-[12px] text-text-muted">
              Nothing is left in here — the filter or the levers moved underneath it.
            </p>
            <button
              onClick={() => setPath({})}
              className="mt-2 text-[11.5px] font-semibold text-accent hover:underline"
            >
              Back to all task domains
            </button>
          </div>
        ) : view === "map" ? (
          <Treemap
            data={data}
            height={HEIGHT}
            maxCells={MAX_CELLS}
            renderCell={(c) => (
              <PoolCell cell={c} path={path} onDrill={setPath} onAdd={addNode} />
            )}
            cellProps={(c) => ({ "data-node": c.datum.payload.key })}
          />
        ) : (
          <div className="max-h-[320px] overflow-y-auto">
            {nodes.map((n) => (
              <div
                key={n.key}
                className="flex items-center gap-2 border-b border-border/60 py-1 last:border-0"
              >
                <button
                  onClick={() => (n.drillable ? drill(setPath, path, n) : addNode(n))}
                  className="min-w-0 flex-1 text-left"
                  title={n.drillable ? "Open" : addTitle(n)}
                >
                  <span className="flex items-center gap-1 truncate text-[11.5px] text-text">
                    {n.name}
                    {n.drillable && <ChevronRight size={10} className="shrink-0 text-text-muted" />}
                  </span>
                  <span className="block truncate text-[10.5px] text-text-muted">{describe(n)}</span>
                </button>
                <span className="shrink-0 text-right text-[11.5px] tabular-nums text-text-secondary">
                  {n.hours_per_week.toFixed(0)} h
                </span>
                <button
                  onClick={() => addNode(n)}
                  title={addTitle(n)}
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

function drill(onPath: (p: RollupPath) => void, path: RollupPath, n: RollupNode) {
  if (n.level === "domain") onPath({ domainId: n.id });
  else if (n.level === "category") onPath({ ...path, categoryId: n.id });
}

/** The sub-line under a node's name — what it is made of, and how automatable it is. */
function describe(n: RollupNode): string {
  const parts: string[] = [];
  if (n.level === "cluster") parts.push(`${n.leaves[0].n_roles} roles`);
  else parts.push(`${n.leaves.length} ${n.level === "domain" ? "task clusters" : "clusters"}`);
  parts.push(n.automation !== null ? `${n.automation.toFixed(0)}% automatable` : "not assessed");
  if (n.level !== "cluster" && n.assessed_leaves < n.leaves.length) {
    parts.push(`${n.leaves.length - n.assessed_leaves} unassessed`);
  }
  return parts.join(" · ");
}

function addTitle(n: RollupNode): string {
  const hours = n.hours_per_holder_week.toFixed(1);
  return n.level === "cluster"
    ? `Add ${hours} h — one holder's share`
    : `Add all ${n.leaves.length} clusters beneath — ${hours} h for one holder`;
}

/** Where you are in the taxonomy, and the way back out. */
function Breadcrumb({
  pool,
  path,
  onPath,
  level,
  count,
}: {
  pool: PoolResult;
  path: RollupPath;
  onPath: (p: RollupPath) => void;
  level: string;
  count: number;
}) {
  // Names come from the data rather than being threaded through the click, so a breadcrumb built
  // from a restored path is still labelled.
  const inScope = pool.clusters.filter(
    (c) =>
      (path.domainId === undefined || c.domain_id === path.domainId) &&
      (path.categoryId === undefined || c.category_id === path.categoryId),
  );
  const domain = path.domainId !== undefined ? inScope[0]?.domain : null;
  const category = path.categoryId !== undefined ? inScope[0]?.category : null;
  const noun = level === "domain" ? "domains" : level === "category" ? "categories" : "clusters";

  return (
    <div className="flex flex-wrap items-center gap-1 border-b border-border bg-panel/60 px-4 py-1.5 text-[10.5px]">
      <button
        onClick={() => onPath({})}
        disabled={path.domainId === undefined}
        className="font-semibold text-accent hover:underline disabled:text-text-muted disabled:no-underline"
      >
        All task domains
      </button>
      {domain && (
        <>
          <ChevronRight size={10} className="text-text-muted" />
          <button
            onClick={() => onPath({ domainId: path.domainId })}
            disabled={path.categoryId === undefined}
            className="font-semibold text-accent hover:underline disabled:text-text-muted disabled:no-underline"
          >
            {domain}
          </button>
        </>
      )}
      {category && (
        <>
          <ChevronRight size={10} className="text-text-muted" />
          <span className="font-semibold text-text-muted">{category}</span>
        </>
      )}
      <span className="ml-auto tabular-nums text-text-muted">
        {count} {noun}
        {level !== "cluster" && " · click to open"}
      </span>
    </div>
  );
}

/** A draggable pool cell. Its position is the data, so the drag overlay does the moving. */
function PoolCell({
  cell,
  path,
  onDrill,
  onAdd,
}: {
  cell: TreemapCell<RollupNode>;
  path: RollupPath;
  onDrill: (p: RollupPath) => void;
  onAdd: (n: RollupNode) => void;
}) {
  const n = cell.datum.payload;
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: `pool-${n.key}`,
    data: { from: "pool", node: n },
  });
  const big = cell.w >= 95 && cell.h >= 30;

  return (
    <div
      ref={setNodeRef}
      {...listeners}
      {...attributes}
      // Enter opens a group and adds a leaf — the same thing a click does, so the keyboard path
      // is the pointer path rather than a reduced version of it. Space always adds, which is the
      // only way to allocate a whole group without drilling into it first.
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          if (n.drillable) drill(onDrill, path, n);
          else onAdd(n);
        } else if (e.key === " ") {
          e.preventDefault();
          onAdd(n);
        }
      }}
      onClick={() => {
        if (n.drillable) drill(onDrill, path, n);
      }}
      tabIndex={0}
      role="button"
      aria-label={ariaFor(n)}
      className={`flex h-full w-full flex-col justify-center outline-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent ${
        n.drillable ? "cursor-pointer" : "cursor-grab"
      }`}
      style={{ opacity: isDragging ? 0.35 : 1 }}
    >
      {big && (
        <span className="truncate text-[10.5px] font-semibold leading-tight">
          {cell.datum.label}
        </span>
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
            onAdd(n);
          }}
          title={addTitle(n)}
          className="absolute right-1 top-1 rounded-[4px] bg-card/80 px-1 text-[10px] font-bold text-accent hover:bg-card"
        >
          +
        </button>
      )}
    </div>
  );
}

function ariaFor(n: RollupNode): string {
  const score =
    n.automation !== null ? `, ${n.automation.toFixed(0)}% automatable` : ", not assessed";
  if (n.level === "cluster") {
    return `${n.name}. ${n.hours_per_week.toFixed(0)} hours a week across the sample, ${n.hours_per_holder_week.toFixed(1)} per holder${score}. Press Enter to add to the job design.`;
  }
  return `${n.name}. ${n.leaves.length} task clusters, ${n.hours_per_week.toFixed(0)} hours a week${score}. Press Enter to open, Space to add all of it.`;
}
