import type { PoolCluster } from "../types/workDesign";

/**
 * Rolling the unreviewed pool up the task taxonomy, so it can be read before it is drilled.
 *
 * A whole-workforce pool is 750 task clusters whose largest is 1% of the total. Flat, that is a
 * texture with no shape to read — the panel had to say so out loud and ask for a filter. Rolled
 * up to the ~14 task domains it is a chart again, and the detail is a click away rather than
 * gone.
 *
 * Entirely client-side: every pool row already carries `domain_id`/`category_id` from the
 * taxonomy, so a drill is instant and costs no round trip. That also means the rollup can never
 * disagree with the pool it came from — it is the same numbers, added up.
 *
 * Hours sum. Scores do not: an automation percentage is a rate, so a group's rate is the
 * **hours-weighted mean of its assessed children**, and unassessed hours are excluded from the
 * weighting rather than counted as zero. A group with nothing assessed reports `null`, which is
 * the distinction the whole app keeps — unknown opportunity is not no opportunity.
 */

export type RollupLevel = "domain" | "category" | "cluster";

/** Where the panel is looking. Empty path is the top: one node per task domain. */
export interface RollupPath {
  domainId?: number;
  categoryId?: number;
}

export interface RollupNode {
  /** Stable within a level — the drag id and React key. */
  key: string;
  id: number;
  name: string;
  level: RollupLevel;
  hours_per_week: number;
  hours_per_holder_week: number;
  share_pct: number;
  /** Hours-weighted mean over assessed leaves; null when none are assessed. */
  automation: number | null;
  augmentation: number | null;
  /** How many of the leaves beneath carry an assessment. */
  assessed_leaves: number;
  /** The real clusters beneath. One entry, itself, at cluster level. */
  leaves: PoolCluster[];
  /** True when clicking drills rather than adds. */
  drillable: boolean;
}

export function levelOf(path: RollupPath): RollupLevel {
  if (path.categoryId !== undefined) return "cluster";
  if (path.domainId !== undefined) return "category";
  return "domain";
}

/** The clusters in scope at `path` — the pool, narrowed by whatever has been drilled into. */
export function scopeOf(clusters: PoolCluster[], path: RollupPath): PoolCluster[] {
  let out = clusters;
  if (path.domainId !== undefined) out = out.filter((c) => c.domain_id === path.domainId);
  if (path.categoryId !== undefined) out = out.filter((c) => c.category_id === path.categoryId);
  return out;
}

/**
 * The nodes to draw at `path`, biggest first.
 *
 * `share_pct` is recomputed against the nodes actually shown rather than carried from the pool,
 * because a drill renormalises: inside one domain, "12% of this domain" is the useful number
 * and "0.4% of the workforce" is not.
 */
export function rollup(clusters: PoolCluster[], path: RollupPath): RollupNode[] {
  const level = levelOf(path);
  const scope = scopeOf(clusters, path);

  if (level === "cluster") {
    const total = scope.reduce((s, c) => s + c.hours_per_week, 0);
    return scope
      .map((c) => ({
        key: `c${c.cluster_id}`,
        id: c.cluster_id,
        name: c.name,
        level: "cluster" as const,
        hours_per_week: c.hours_per_week,
        hours_per_holder_week: c.hours_per_holder_week,
        share_pct: total ? (100 * c.hours_per_week) / total : 0,
        automation: c.automation,
        augmentation: c.augmentation,
        assessed_leaves: c.assessed ? 1 : 0,
        leaves: [c],
        drillable: false,
      }))
      .sort((a, b) => b.hours_per_week - a.hours_per_week);
  }

  const keyOf = (c: PoolCluster) => (level === "domain" ? c.domain_id : c.category_id);
  const nameOf = (c: PoolCluster) => (level === "domain" ? c.domain : c.category);

  const groups = new Map<number, PoolCluster[]>();
  for (const c of scope) {
    const k = keyOf(c);
    const g = groups.get(k);
    if (g) g.push(c);
    else groups.set(k, [c]);
  }

  const nodes: RollupNode[] = [];
  for (const [id, leaves] of groups) {
    const hours = leaves.reduce((s, c) => s + c.hours_per_week, 0);
    nodes.push({
      key: level === "domain" ? `d${id}` : `k${id}`,
      id,
      // A cluster whose taxonomy row is missing rolls up under an honest label rather than
      // silently joining whichever group happens to hold id -1.
      name: nameOf(leaves[0]) || (level === "domain" ? "Uncategorised domain" : "Uncategorised"),
      level,
      hours_per_week: hours,
      hours_per_holder_week: leaves.reduce((s, c) => s + c.hours_per_holder_week, 0),
      share_pct: 0,
      automation: weightedScore(leaves, "automation"),
      augmentation: weightedScore(leaves, "augmentation"),
      assessed_leaves: leaves.filter((c) => c.assessed).length,
      leaves,
      // A group of one has nothing to drill into — clicking it should add it, not open a level
      // showing the same single tile again.
      drillable: leaves.length > 1,
    });
  }

  const total = nodes.reduce((s, n) => s + n.hours_per_week, 0);
  for (const n of nodes) n.share_pct = total ? (100 * n.hours_per_week) / total : 0;
  return nodes.sort((a, b) => b.hours_per_week - a.hours_per_week);
}

/** Hours-weighted mean of one score over the assessed leaves. `null` when none are assessed. */
function weightedScore(leaves: PoolCluster[], field: "automation" | "augmentation"): number | null {
  let num = 0;
  let den = 0;
  for (const c of leaves) {
    const v = c[field];
    if (!c.assessed || v === null) continue;
    num += v * c.hours_per_week;
    den += c.hours_per_week;
  }
  // Weighting by hours means a group of assessed-but-zero-hour clusters has no weight to
  // average with. Fall back to the plain mean rather than reporting it unassessed.
  if (den > 0) return num / den;
  const scored = leaves.filter((c) => c.assessed && c[field] !== null);
  if (!scored.length) return null;
  return scored.reduce((s, c) => s + (c[field] as number), 0) / scored.length;
}
