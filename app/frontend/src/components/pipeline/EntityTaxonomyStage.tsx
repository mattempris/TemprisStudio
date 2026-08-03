import { useCallback, useEffect, useState } from "react";
import { Play } from "lucide-react";
import type { JobHandle, TaxonomyNode, TierName, TierStatus } from "../../types/pipeline";
import { TaxonomyBrowser, normalizeTaxonomy, type TaxonomyKind } from "./TaxonomyBrowser";
import { TierClusterStage } from "./TierClusterStage";
import type { TierApi } from "../../services/pipelineApi";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";

/**
 * Skills (steps 8-9) and tasks (step 10) follow the same moves: infer from job
 * profiles, group into a three-tier taxonomy, browse the result. Only the entity,
 * the labels and the extra proficiency step differ, so this drives both rather than
 * the flow being written out twice.
 *
 * The grouping is the same per-tier flow the job hierarchy uses — clusters, then
 * categories, then families, each with its own cluster count, its own stability gate
 * with the routing cost shown before it is paid, and its own naming to confirm. It
 * replaced a single panel with three k sliders and one confirm that named every
 * level at once: that gave no way to see what a level contained before committing to
 * the level above it, and no way to fix one tier without redoing all three.
 *
 * All three tiers live inside this one wizard step rather than becoming three steps
 * of their own. The job hierarchy earns three steps because its levels are the
 * deliverable; here the deliverable is the taxonomy, and the tiers are how it is
 * built.
 */

interface Props {
  kind: TaxonomyKind;
  /** [family/domain, category, cluster] — used by the browser's tier headings. */
  tierLabels: [string, string, string];
  inferredCount: number;
  profilesCovered: number;
  jobProfileCount: number;
  named: boolean;
  /** Per-tier status, keyed by tier. Undefined until the first fetch lands. */
  tiers: Partial<Record<TierName, TierStatus>>;
  audit: Record<string, number>;
  onInfer: () => Promise<JobHandle>;
  tierApi: (tier: TierName) => TierApi;
  loadTaxonomy: () => Promise<{ roots: TaxonomyNode[]; hasHeadcount: boolean }>;
  runJob: (start: () => Promise<JobHandle>) => void;
  busy: boolean;
  progress: React.ReactNode;
  /** Inline spinner + heartbeat for whichever tier button is running. */
  activity?: React.ReactNode;
  /** Skills only: proficiency criteria generation plus the auto-map onto jobs.
   *  `editor` is the template editor, shown above the generate button since it
   *  defines the rubric that generation is written against. */
  proficiency?: {
    done: boolean;
    mappedClusters: number;
    levelsAssigned: number;
    requirements: number;
    onGenerate: () => Promise<JobHandle>;
    editor?: React.ReactNode;
  };
}

/** Finest first — the order they are confirmed in. */
const TIER_ORDER: TierName[] = ["profile", "category", "family"];

export function EntityTaxonomyStage({
  kind,
  tierLabels,
  inferredCount,
  profilesCovered,
  jobProfileCount,
  named,
  tiers,
  audit,
  onInfer,
  tierApi,
  loadTaxonomy,
  runJob,
  busy,
  progress,
  activity,
  proficiency,
}: Props) {
  const [tree, setTree] = useState<{ roots: TaxonomyNode[]; hasHeadcount: boolean } | null>(null);
  // Whether the running job is this step's inference, as opposed to one of the
  // tiers' own jobs — which report next to their own buttons.
  const [ranInfer, setRanInfer] = useState(false);

  const refresh = useCallback(async () => {
    if (!named) return;
    try {
      setTree(await loadTaxonomy());
    } catch {
      setTree(null);
    }
  }, [named, loadTaxonomy]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const noun = kind === "skill" ? "skills" : "tasks";

  return (
    <div className="space-y-4">
      {/* 1. Infer */}
      <div className="space-y-2">
        <Button
          variant="primary"
          onClick={() => {
            setRanInfer(true);
            runJob(onInfer);
          }}
          disabled={busy || jobProfileCount === 0}
        >
          <span className="flex items-center gap-1.5">
            <Play size={12} />
            {inferredCount > 0 ? `Re-infer ${noun}` : `Infer ${noun}`} from {jobProfileCount} job
            profile{jobProfileCount === 1 ? "" : "s"}
          </span>
        </Button>
        {inferredCount > 0 && (
          <p className="text-[12.5px] text-text-secondary">
            {inferredCount} {noun} across {profilesCovered} profiles
            {audit && Object.keys(audit).length > 0 && <AuditLine kind={kind} audit={audit} />}
          </p>
        )}
      </div>

      {/* The step-level bar covers inference only. Each tier renders its own
          progress next to the button that started it, so a taxonomy view never
          shows two spinners for one job. */}
      {ranInfer && progress}

      {/* 2. Group into the three tiers, bottom-up. Each is confirmed on its own,
             so a coarser tier only appears once the one below it is settled. */}
      {inferredCount >= 3 &&
        TIER_ORDER.map((tier, i) => {
          const st = tiers[tier];
          if (!st) return null;
          // Hide a tier that cannot run yet AND has nothing below it in progress:
          // showing all three greyed out at once buries the one to act on.
          const below = i === 0 ? null : tiers[TIER_ORDER[i - 1]];
          if (!st.ready_to_run && !(below?.confirmed ?? true)) return null;
          const api = tierApi(tier);
          return (
            <div key={tier} className="rounded-[10px] border border-border bg-panel px-4 py-3">
              <p className="mb-2 text-[11px] font-extrabold uppercase tracking-wider text-text-muted">
                {i + 1}. {st.title}
              </p>
              <TierClusterStage
                status={st}
                preview={api.preview}
                gatePreview={api.gate}
                onBuild={() => api.build()}
                onAnalyse={api.analyse}
                onConfirm={(k, gate) => api.confirm(k, gate)}
                loadClusters={api.clusters}
                loadClusterMembers={api.clusterMembers}
                onRename={api.rename}
                onReassign={api.reassign}
                runJob={runJob}
                busy={busy}
                activity={activity}
                // One progress bar for the step, rendered above, rather than one
                // per tier: only one tier can be running at a time anyway.
                progress={null}
              />
            </div>
          );
        })}

      {/* 3. Proficiency (skills only) */}
      {named && proficiency && (
        <div className="space-y-2.5 rounded-[10px] border border-border bg-panel px-4 py-3">
          <div>
            <div className="flex items-center gap-2">
              <p className="text-[12.5px] font-semibold text-text">Proficiency levels</p>
              <Badge color="teal">optional</Badge>
            </div>
            <p className="mt-0.5 text-[11.5px] leading-snug text-text-secondary">
              Generates level criteria per skill cluster, then deterministically maps each job
              profile onto them. The taxonomy is complete without this — run it when you want
              levels against each job's skills, and skip it if you do not.
            </p>
          </div>
          {proficiency.editor}
          <Button variant="primary" onClick={() => runJob(proficiency.onGenerate)} disabled={busy}>
            <span className="flex items-center gap-1.5">
              <Play size={12} />
              {proficiency.done ? "Regenerate proficiency mapping" : "Generate proficiency and map jobs"}
            </span>
          </Button>
          {proficiency.mappedClusters > 0 && (
            <p className="mt-2 text-[11.5px] text-text-secondary">
              {proficiency.mappedClusters} clusters have level criteria;{" "}
              {proficiency.levelsAssigned} of {proficiency.requirements} job-skill requirements
              have an assigned level.
            </p>
          )}
        </div>
      )}

      {/* 4. Browse */}
      {tree && tree.roots.length > 0 && (
        <TaxonomyBrowser
          kind={kind}
          roots={normalizeTaxonomy(tree.roots, kind)}
          hasHeadcount={tree.hasHeadcount}
          tierLabels={tierLabels}
        />
      )}
    </div>
  );
}

function AuditLine({ kind, audit }: { kind: TaxonomyKind; audit: Record<string, number> }) {
  // The audits measure the spec's own constraints (skills must read as
  // attributes not tasks; task proportions must sum to 100). Surfaced because a
  // high number means the prompt has drifted, which is invisible otherwise.
  const parts: string[] = [];
  if (kind === "skill") {
    if (audit.task_phrased) parts.push(`${audit.task_phrased} phrased as tasks`);
    if (audit.name_too_long) parts.push(`${audit.name_too_long} names over 3 words`);
    if (audit.description_out_of_range) parts.push(`${audit.description_out_of_range} descriptions off-length`);
  } else {
    if (audit.jobs_needing_proportion_fix)
      parts.push(`${audit.jobs_needing_proportion_fix} jobs rescaled to 100%`);
    if (audit.name_out_of_range) parts.push(`${audit.name_out_of_range} names outside 2-4 words`);
  }
  if (!parts.length) return null;
  return <span className="text-text-muted"> — {parts.join(", ")}</span>;
}
