import { useMemo, useState } from "react";
import type { ClusterOpportunity, RoleTask } from "../../types/workforce";
import { Treemap, type TreemapDatum } from "./Treemap";
import { TreemapTooltip } from "./TreemapTooltip";

/**
 * How a role spends its week, as a treemap — the Task Map pattern from the insurance demo,
 * rebuilt on this app's data and colour scale.
 *
 * A ranked list of tasks with a percentage column answers "which task is most automatable"
 * and hides the thing a client actually reacts to: the *shape* of the week. A role with one
 * task at 40% and eight at 7.5% reads identically to a role with nine even tasks when both
 * are twelve rows of text. Sized cells make that instant, and put the automation score where
 * it belongs — on the area it applies to, so a hot sliver cannot be mistaken for a big win.
 *
 * Now an adapter over the generic `Treemap`. Its props are unchanged, deliberately: the
 * opportunity step must look and behave exactly as it did, and keeping this signature is
 * what makes that verifiable rather than hoped for. What it still owns is everything
 * specific to a role's tasks — the automation/augmentation switch, the caption, and the
 * tooltip's contents. Layout, heat, hatching and label thresholds moved to the primitive.
 */

type Metric = "automation" | "augmentation";

// Taller than a cell needs to be legible, because a role has a dozen tasks and the small
// ones still have to be readable.
const HEIGHT = 230;

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
  const [hover, setHover] = useState<{ task: RoleTask; x: number; y: number } | null>(null);

  // Tasks with no assessed cluster have no score. They still take up the person's week, so
  // they keep their area and render hatched rather than being dropped — a treemap that
  // silently omitted them would overstate how much of the role has been assessed.
  const data = useMemo<TreemapDatum<RoleTask>[]>(
    () =>
      tasks.map((t, i) => ({
        id: `${t.cluster_id}-${i}`,
        value: t.proportion,
        label: t.name,
        sub:
          `${t.proportion.toFixed(1)}%` +
          (heat && (metric === "automation" ? t.automation : t.augmentation) !== null
            ? ` · ${Math.round((metric === "automation" ? t.automation : t.augmentation) as number)}%`
            : ""),
        score: metric === "automation" ? t.automation : t.augmentation,
        payload: t,
      })),
    [tasks, metric, heat],
  );

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

      <div onMouseLeave={() => setHover(null)}>
        <Treemap
          data={data}
          height={HEIGHT}
          heat={heat}
          emptyMessage="No task proportions recorded for this role."
          cellProps={(c) => ({
            onMouseEnter: (e: React.MouseEvent) =>
              setHover({ task: c.datum.payload, x: e.clientX, y: e.clientY }),
            onMouseMove: (e: React.MouseEvent) =>
              setHover({ task: c.datum.payload, x: e.clientX, y: e.clientY }),
          })}
        />
      </div>

      {hover && (
        <TreemapTooltip
          task={hover.task}
          actions={actionsByCluster.get(hover.task.cluster_id) ?? []}
          x={hover.x}
          y={hover.y}
        />
      )}
    </div>
  );
}
