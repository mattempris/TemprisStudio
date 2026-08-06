import { useMemo, useState } from "react";
import { ChevronRight, Sparkles } from "lucide-react";
import type { TaxonomyLeaf, TaxonomyNode, TierName } from "../../types/pipeline";
import { Badge } from "../ui/Badge";
import { cn } from "../../lib/cn";

/**
 * Browsable three-tier taxonomy, shared by skills (step 9) and tasks (step 10).
 *
 * Both are Family/Domain › Category › Cluster with a leaf list, so one component
 * takes the tier labels and an analytics renderer rather than being written
 * twice. The aggregate-first discipline from the JE browser applies here too:
 * every tier shows its rolled-up numbers and nothing expands until asked.
 *
 * **Editable in place** when `edit` is supplied. Renaming and re-parenting already existed on the
 * per-tier confirm panels above, and were reported as missing anyway — because this is the view
 * that looks like the architecture, so this is where someone goes to correct it. Asking them to
 * find the right numbered accordion, expand it, expand a group inside it and use an unlabelled
 * dropdown was a worse answer than putting the two controls on the rows they describe.
 *
 * Both edits are label changes: nothing is re-embedded and no tier is invalidated. Moving a
 * category to another family is `reassign` on the FAMILY tier — a tier owns the placement of its
 * children, so the call is always made one level above the row being moved.
 */

export type TaxonomyKind = "skill" | "task";

/**
 * Fold the two backend shapes into the one this component renders.
 *
 * Skills come back as families › categories › clusters › skills, tasks as
 * domains › categories › clusters › tasks. The tier semantics are identical, so
 * the difference is renamed away here rather than branched on in every row.
 */
export function normalizeTaxonomy(roots: unknown[], kind: TaxonomyKind): TaxonomyNode[] {
  const leafKey = kind === "skill" ? "skills" : "tasks";
  const walk = (node: Record<string, unknown>, depth: number): TaxonomyNode => {
    const childKey = depth === 2 ? leafKey : depth === 1 ? "clusters" : "categories";
    const raw = (node[childKey] as Record<string, unknown>[] | undefined) ?? [];
    return {
      ...(node as unknown as TaxonomyNode),
      children: depth < 2 ? raw.map((c) => walk(c, depth + 1)) : undefined,
      leaves: depth === 2 ? (raw as unknown as TaxonomyLeaf[]) : undefined,
    };
  };
  return roots.map((r) => walk(r as Record<string, unknown>, 0));
}

/** Renaming a cluster, and re-parenting one into a different group. */
export interface TaxonomyEdit {
  rename: (tier: TierName, clusterId: number, name: string) => Promise<unknown>;
  /** `tier` is the PARENT tier — the one that owns where its children sit. */
  reassign: (tier: TierName, itemId: string, clusterId: number) => Promise<unknown>;
  onChanged: () => void;
}

interface Props {
  kind: TaxonomyKind;
  roots: TaxonomyNode[];
  hasHeadcount: boolean;
  tierLabels: [string, string, string];
  /** Omit for a read-only tree. */
  edit?: TaxonomyEdit;
}

/** Depth in this tree to the tier name the API uses. Finest last, matching the API's order. */
const TIER_AT: [TierName, TierName, TierName] = ["family", "category", "profile"];

export function TaxonomyBrowser({ kind, roots, hasHeadcount, tierLabels, edit }: Props) {
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const toggle = (k: string) => setOpen((o) => ({ ...o, [k]: !o[k] }));
  const [busy, setBusy] = useState<string | null>(null);

  /** Where a row at `depth` could be moved to: its parent tier's clusters. */
  const parents = useMemo(
    () => ({
      1: roots.map((r) => ({ id: r.id, name: r.name })),
      2: roots.flatMap((r) => (r.children ?? []).map((c) => ({ id: c.id, name: c.name }))),
    }),
    [roots],
  );

  /** What a row at `depth` can do. A family has no parent, so it gets rename only. */
  function rowEdit(depth: number, node: TaxonomyNode, parentId: number | null): RowEdit | undefined {
    if (!edit) return undefined;
    const tier = TIER_AT[depth];
    const key = `${tier}:${node.id}`;
    return {
      busy: busy === key,
      onRename: async (name) => {
        setBusy(key);
        try {
          await edit.rename(tier, node.id, name);
          edit.onChanged();
        } finally {
          setBusy(null);
        }
      },
      // Moving is always a call on the tier ABOVE: that tier owns where its children sit, and
      // the item id it knows this row by is "<this tier>:<id>".
      move:
        depth === 0
          ? undefined
          : {
              currentId: parentId,
              options: parents[depth as 1 | 2],
              onMove: async (to) => {
                setBusy(key);
                try {
                  await edit.reassign(TIER_AT[depth - 1], `${tier}:${node.id}`, to);
                  edit.onChanged();
                } finally {
                  setBusy(null);
                }
              },
            },
    };
  }

  const totals = useMemo(() => {
    const leafCount = (n: TaxonomyNode) => n.skill_count ?? n.task_count ?? 0;
    return {
      leaves: roots.reduce((a, r) => a + leafCount(r), 0),
      proportion: roots.reduce((a, r) => a + (r.proportion_sum ?? 0), 0),
      fte: roots.reduce((a, r) => a + (r.fte_equivalent ?? 0), 0),
    };
  }, [roots]);

  if (!roots.length) {
    return <p className="text-[12.5px] text-text-muted">Nothing clustered yet.</p>;
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-1 rounded-[10px] bg-panel px-4 py-2.5">
        <Stat label={tierLabels[0]} value={roots.length} />
        <Stat label={kind === "skill" ? "Skills" : "Tasks"} value={totals.leaves} />
        {kind === "task" && (
          <Stat label="Time proportion" value={`${totals.proportion.toFixed(0)}%`} />
        )}
        {hasHeadcount && kind === "task" && totals.fte > 0 && (
          <Stat label="FTE equivalent" value={totals.fte.toFixed(1)} />
        )}
      </div>

      <ul className="space-y-1.5">
        {roots.map((root) => {
          const rk = `r${root.id}`;
          return (
            <li key={rk} className="overflow-hidden rounded-[10px] border border-border bg-card">
              <Row
                depth={0}
                node={root}
                kind={kind}
                hasHeadcount={hasHeadcount}
                expanded={!!open[rk]}
                onToggle={() => toggle(rk)}
                edit={rowEdit(0, root, null)}
              />
              {open[rk] &&
                (root.children ?? []).map((cat) => {
                  const ck = `${rk}c${cat.id}`;
                  return (
                    <div key={ck} className="border-t border-border">
                      <Row
                        depth={1}
                        node={cat}
                        kind={kind}
                        hasHeadcount={hasHeadcount}
                        expanded={!!open[ck]}
                        onToggle={() => toggle(ck)}
                        edit={rowEdit(1, cat, root.id)}
                      />
                      {open[ck] &&
                        (cat.children ?? []).map((cl) => {
                          const lk = `${ck}l${cl.id}`;
                          return (
                            <div key={lk} className="border-t border-border bg-panel/40">
                              <Row
                                depth={2}
                                node={cl}
                                kind={kind}
                                hasHeadcount={hasHeadcount}
                                expanded={!!open[lk]}
                                onToggle={() => toggle(lk)}
                                edit={rowEdit(2, cl, cat.id)}
                              />
                              {open[lk] && (
                                <LeafTable
                                  kind={kind}
                                  node={cl}
                                  hasHeadcount={hasHeadcount}
                                />
                              )}
                            </div>
                          );
                        })}
                    </div>
                  );
                })}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <span className="flex items-baseline gap-1.5">
      <span className="text-[15px] font-bold tabular-nums text-accent">{value}</span>
      <span className="text-[10px] font-semibold uppercase tracking-wider text-text-muted">
        {label}
      </span>
    </span>
  );
}

export interface RowEdit {
  busy: boolean;
  onRename: (name: string) => Promise<void>;
  /** Absent on a family row, which has no parent to move between. */
  move?: {
    currentId: number | null;
    options: { id: number; name: string }[];
    onMove: (to: number) => Promise<void>;
  };
}

function Row({
  depth,
  node,
  kind,
  hasHeadcount,
  expanded,
  onToggle,
  edit,
}: {
  depth: number;
  node: TaxonomyNode;
  kind: TaxonomyKind;
  hasHeadcount: boolean;
  expanded: boolean;
  onToggle: () => void;
  edit?: RowEdit;
}) {
  const count = node.skill_count ?? node.task_count ?? 0;
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState(node.name);

  // A div, not a button. The row now contains an input and a select, and a button cannot hold
  // either — the browser hoists them out and the layout collapses.
  return (
    <div
      className={cn(
        "flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-panel",
        depth === 0 && "font-semibold",
      )}
      style={{ paddingLeft: `${12 + depth * 18}px` }}
    >
      <button onClick={onToggle} className="shrink-0" aria-label={expanded ? "Collapse" : "Expand"}>
        <ChevronRight
          size={13}
          className={cn("text-text-muted transition-transform", expanded && "rotate-90")}
        />
      </button>
      {edit && renaming ? (
        <input
          autoFocus
          value={draft}
          disabled={edit.busy}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              setDraft(node.name);
              setRenaming(false);
            }
            if (e.key === "Enter" && draft.trim() && draft.trim() !== node.name) {
              void edit.onRename(draft.trim()).then(() => setRenaming(false));
            } else if (e.key === "Enter") {
              setRenaming(false);
            }
          }}
          onBlur={() => {
            setDraft(node.name);
            setRenaming(false);
          }}
          className="min-w-0 flex-1 rounded-[6px] border border-accent bg-card px-2 py-0.5 text-[12.5px] font-semibold text-text outline-none"
        />
      ) : (
        <button
          onClick={() => (edit ? (setDraft(node.name), setRenaming(true)) : onToggle())}
          title={edit ? "Click to rename" : undefined}
          className={cn(
            "min-w-0 flex-1 truncate text-left",
            depth === 0 ? "text-[13px] text-text" : "text-[12.5px] text-text-secondary",
            edit && "rounded-[4px] hover:bg-card",
          )}
        >
          {node.name}
        </button>
      )}
      {edit?.move && edit.move.options.length > 1 && (
        <span className="flex shrink-0 items-center gap-1">
          <span className="text-[10px] text-text-muted">move to</span>
          <select
            value={edit.move.currentId ?? ""}
            disabled={edit.busy}
            title="Move to another group"
            onChange={(e) => {
              const to = Number(e.target.value);
              if (to !== edit.move!.currentId) void edit.move!.onMove(to);
            }}
            className="max-w-[11rem] rounded-[6px] border border-border bg-card px-1 py-0.5 text-[10.5px] text-text-secondary outline-none hover:border-accent focus:border-accent disabled:opacity-50"
          >
            {edit.move.options.map((o) => (
              <option key={o.id} value={o.id}>
                {o.name}
              </option>
            ))}
          </select>
        </span>
      )}

      <span className="flex shrink-0 items-center gap-3 tabular-nums">
        {kind === "task" && node.proportion_sum != null && (
          <Metric value={`${node.proportion_sum.toFixed(1)}%`} label="time" />
        )}
        {kind === "task" && hasHeadcount && node.fte_equivalent != null && (
          <Metric value={node.fte_equivalent.toFixed(1)} label="FTE" />
        )}
        {/* Zero is not a fact worth a column here: it means the analytic has not
            been produced yet, not that no job needs the skill. Showing "0 JOBS"
            against every branch read as broken data rather than absent data. */}
        {kind === "skill" && !!node.jobs_requiring_count && (
          <Metric value={node.jobs_requiring_count} label="jobs" />
        )}
        {kind === "skill" && hasHeadcount && !!node.headcount_requiring && (
          <Metric value={node.headcount_requiring} label="people" />
        )}
        <Metric value={count} label={kind === "skill" ? "skills" : "tasks"} />
      </span>
    </div>
  );
}

function Metric({ value, label }: { value: string | number; label: string }) {
  return (
    <span className="flex items-baseline gap-1">
      <span className="text-[12px] font-bold text-text">{value}</span>
      <span className="text-[9.5px] font-semibold uppercase tracking-wider text-text-muted">
        {label}
      </span>
    </span>
  );
}

function LeafTable({
  kind,
  node,
  hasHeadcount,
}: {
  kind: TaxonomyKind;
  node: TaxonomyNode;
  hasHeadcount: boolean;
}) {
  const leaves = (node.leaves ?? []) as TaxonomyLeaf[];
  const proficiency = node.proficiency_definitions ?? {};
  const [showProficiency, setShowProficiency] = useState(false);

  return (
    <div className="border-t border-border bg-card px-4 py-3">
      {kind === "skill" && Object.keys(proficiency).length > 0 && (
        <div className="mb-3">
          <button
            onClick={() => setShowProficiency((v) => !v)}
            className="flex items-center gap-1.5 text-[11px] font-bold text-accent hover:underline"
          >
            <Sparkles size={11} />
            {showProficiency ? "Hide" : "Show"} proficiency definitions
          </button>
          {showProficiency && (
            <dl className="mt-2 space-y-1.5 rounded-[8px] bg-panel px-3 py-2.5">
              {Object.entries(proficiency).map(([level, text]) => (
                <div key={level} className="flex gap-2 text-[11.5px]">
                  <dt className="w-24 shrink-0 font-bold text-text">{level}</dt>
                  <dd className="text-text-secondary">{text}</dd>
                </div>
              ))}
            </dl>
          )}
        </div>
      )}

      <table className="w-full text-[11.5px]">
        <thead>
          <tr className="border-b border-border text-left text-[9.5px] uppercase tracking-wider text-text-muted">
            <th className="pb-1.5 font-extrabold">{kind === "skill" ? "Skill" : "Task"}</th>
            <th className="pb-1.5 font-extrabold">From anchor role</th>
            {kind === "task" && <th className="pb-1.5 text-right font-extrabold">Time</th>}
            {kind === "task" && hasHeadcount && (
              <th className="pb-1.5 text-right font-extrabold">FTE</th>
            )}
            <th className="pb-1.5 text-right font-extrabold">Stability</th>
          </tr>
        </thead>
        <tbody>
          {leaves.map((leaf) => (
            <tr key={leaf.id} className="border-b border-border/60 last:border-0 align-top">
              <td className="py-1.5 pr-3">
                <span className="font-semibold text-text">{leaf.name}</span>
                {leaf.kind && (
                  <Badge
                    color={leaf.kind === "technical" ? "teal" : "purple"}
                    className="ml-1.5 align-middle"
                  >
                    {leaf.kind === "technical" ? "tech" : "behav"}
                  </Badge>
                )}
                <p className="mt-0.5 text-[11px] leading-snug text-text-muted">{leaf.description}</p>
              </td>
              <td className="py-1.5 pr-3 text-text-secondary">{leaf.source_profile_key}</td>
              {kind === "task" && (
                <td className="py-1.5 text-right tabular-nums text-text">
                  {leaf.proportion?.toFixed(1)}%
                </td>
              )}
              {kind === "task" && hasHeadcount && (
                <td className="py-1.5 text-right tabular-nums text-text-secondary">
                  {leaf.fte_equivalent?.toFixed(2) ?? "—"}
                </td>
              )}
              <td className="py-1.5 text-right tabular-nums">
                {/* An LLM-routed item is one the geometry was unsure about — worth
                    seeing, since it's where a placement is most arguable. */}
                {leaf.routed_by_llm ? (
                  <Badge color="warning">routed</Badge>
                ) : (
                  <span className="text-text-muted">
                    {leaf.stability_score?.toFixed(2) ?? "—"}
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
