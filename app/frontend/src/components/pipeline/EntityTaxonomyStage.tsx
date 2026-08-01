import { useCallback, useEffect, useState } from "react";
import { Play } from "lucide-react";
import type { ClusterPreview, JobHandle, TaxonomyNode } from "../../types/pipeline";
import { ClusterKPanel, type TierLabels } from "./ClusterKPanel";
import { TaxonomyBrowser, normalizeTaxonomy, type TaxonomyKind } from "./TaxonomyBrowser";
import { Button } from "../ui/Button";

/**
 * Skills (steps 8-9) and tasks (step 10) follow the same four moves: infer from
 * job profiles, embed and build the Ward tree, pick cluster counts, browse the
 * result. Only the entity, the labels and the extra proficiency step differ, so
 * this drives both rather than the flow being written out twice.
 */

interface Props {
  kind: TaxonomyKind;
  labels: TierLabels;
  /** [family/domain, category, cluster] — used by the browser's tier headings. */
  tierLabels: [string, string, string];
  inferredCount: number;
  profilesCovered: number;
  jobProfileCount: number;
  clustered: boolean;
  named: boolean;
  k: { families: number | null; categories: number | null; clusters: number | null };
  audit: Record<string, number>;
  onInfer: () => Promise<JobHandle>;
  onBuildTree: () => Promise<JobHandle>;
  preview: (k: { families: number; categories: number; profiles: number }) => Promise<ClusterPreview>;
  onConfirm: (k: { families: number; categories: number; profiles: number }, gate: number) => Promise<JobHandle>;
  loadTaxonomy: () => Promise<{ roots: TaxonomyNode[]; hasHeadcount: boolean }>;
  runJob: (start: () => Promise<JobHandle>) => void;
  busy: boolean;
  progress: React.ReactNode;
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

export function EntityTaxonomyStage({
  kind,
  labels,
  tierLabels,
  inferredCount,
  profilesCovered,
  jobProfileCount,
  clustered,
  named,
  k,
  audit,
  onInfer,
  onBuildTree,
  preview,
  onConfirm,
  loadTaxonomy,
  runJob,
  busy,
  progress,
  proficiency,
}: Props) {
  const [tree, setTree] = useState<{ roots: TaxonomyNode[]; hasHeadcount: boolean } | null>(null);

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
        <Button variant="primary" onClick={() => runJob(onInfer)} disabled={busy || jobProfileCount === 0}>
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

      {/* 2. Build the tree */}
      {inferredCount >= 3 && !clustered && (
        <Button variant="primary" onClick={() => runJob(onBuildTree)} disabled={busy}>
          <span className="flex items-center gap-1.5">
            <Play size={12} /> Embed and build the cluster tree
          </span>
        </Button>
      )}

      {progress}

      {/* 3. Choose cluster counts */}
      {clustered && (
        <ClusterKPanel
          itemCount={inferredCount}
          labels={labels}
          initial={{
            families: k.families ?? Math.max(2, Math.min(8, Math.floor(inferredCount / 15) || 2)),
            categories: k.categories ?? Math.max(3, Math.min(20, Math.floor(inferredCount / 6) || 3)),
            profiles: k.clusters ?? Math.max(4, Math.min(50, Math.floor(inferredCount / 3) || 4)),
          }}
          preview={preview}
          onConfirm={(kk, gate) => runJob(() => onConfirm(kk, gate))}
          confirming={busy}
        />
      )}

      {/* 4. Proficiency (skills only) */}
      {named && proficiency && (
        <div className="space-y-2.5 rounded-[10px] border border-border bg-panel px-4 py-3">
          <div>
            <p className="text-[12.5px] font-semibold text-text">Proficiency levels</p>
            <p className="mt-0.5 text-[11.5px] leading-snug text-text-secondary">
              Generates level criteria per skill cluster, then deterministically maps each job
              profile onto them.
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

      {/* 5. Browse */}
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
