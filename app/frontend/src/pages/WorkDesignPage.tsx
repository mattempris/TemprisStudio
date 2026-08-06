import { useCallback, useEffect, useMemo, useState } from "react";
import {
  DndContext,
  DragOverlay,
  KeyboardSensor,
  MeasuringStrategy,
  MouseSensor,
  TouchSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
} from "@dnd-kit/core";
import { snapCenterToCursor } from "@dnd-kit/modifiers";
import { workforceApi, studioGates } from "../services/workforceApi";
import { workDesignApi, type JobPayload } from "../services/workDesignApi";
import type { WorkforceStatus } from "../types/workforce";
import {
  EMPTY_FACETS,
  type DesignedJob,
  type DesignedTaskLine,
  type Levers,
  type PoolCluster,
  type PoolResult,
  type TargetProfile,
  type WorkDesignFacetOptions,
  type WorkDesignFacets,
  type WorkDesignStatus,
} from "../types/workDesign";
import { StudioToggle } from "../components/wizard/StudioToggle";
import { DemoReset } from "../components/wizard/DemoReset";
import { Modal } from "../components/ui/Modal";
import { Button } from "../components/ui/Button";
import { FacetBar } from "../components/workdesign/FacetBar";
import { useIsNarrow } from "../hooks/useIsNarrow";
import { PoolPanel } from "../components/workdesign/PoolPanel";
import { JobDesignPanel } from "../components/workdesign/JobDesignPanel";
import { LeversPanel } from "../components/workdesign/LeversPanel";
import { DesignedJobList } from "../components/workdesign/DesignedJobList";

/**
 * Work Design Studio — compose new job definitions out of the work architecture.
 *
 * A workbench, not a wizard: no accordion, because the whole point is that the work pool, the
 * job being designed and the levers are visible at once. The other two studios are accordions
 * because their steps are sequential.
 *
 * Five panels in two rows. Top: the unreviewed work, the job being designed, the levers.
 * Bottom, where results land: the accumulated target profile and the job definitions.
 *
 * The invariant the whole studio rests on, and the first thing to check when a number looks
 * wrong — reported by the API as `conservation_check`, which should read ~0:
 *
 *     as_is == absorbed_by_agents + saved_by_augmentations
 *              + allocated_to_jobs + remaining
 */

const ANCHORS = [
  { id: "wd-pool", title: "Unreviewed work" },
  { id: "wd-design", title: "Job design" },
  { id: "wd-target", title: "Target profile" },
  { id: "wd-jobs", title: "Job definitions" },
] as const;

let lineSeq = 0;
function newLineId() {
  lineSeq += 1;
  return `tmp-${Date.now().toString(36)}-${lineSeq}`;
}

/**
 * "Apply the top X augmentations per role", resolved to skill ids.
 *
 * Per role rather than per cluster because a skill is written for one (role, cluster) pair and
 * only speeds up the hours it was measured against. Ranked by `rank_score`, which is that
 * role's own proportion times the cluster's augmentation — the time each one frees.
 */
function topSkillsPerRole(levers: Levers | null, topN: number): string[] {
  if (!levers || topN <= 0) return [];
  const byRole = new Map<string, Levers["augmentations"]>();
  for (const s of levers.augmentations) {
    const list = byRole.get(s.role_title);
    if (list) list.push(s);
    else byRole.set(s.role_title, [s]);
  }
  return [...byRole.values()].flatMap((l) =>
    [...l].sort((a, b) => b.rank_score - a.rank_score).slice(0, topN).map((s) => s.id),
  );
}

export function WorkDesignPage({
  clientSlug,
  projectSlug,
}: {
  clientSlug: string;
  projectSlug: string;
}) {
  // Memoised for the reason WorkforcePage spells out: an unmemoised client is a new object every
  // render, and anything depending on it becomes a render loop.
  const api = useMemo(() => workDesignApi(clientSlug, projectSlug), [clientSlug, projectSlug]);
  const wfApi = useMemo(() => workforceApi(clientSlug, projectSlug), [clientSlug, projectSlug]);

  const [wfStatus, setWfStatus] = useState<WorkforceStatus | null>(null);
  const [status, setStatus] = useState<WorkDesignStatus | null>(null);
  const [options, setOptions] = useState<WorkDesignFacetOptions | null>(null);
  const [levers, setLevers] = useState<Levers | null>(null);
  const [pool, setPool] = useState<PoolResult | null>(null);
  const [jobs, setJobs] = useState<DesignedJob[]>([]);
  const [target, setTarget] = useState<TargetProfile | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [facets, setFacets] = useState<WorkDesignFacets>(EMPTY_FACETS);
  // `selected` is what the user has ticked; `applied` is what produced the pool on screen. The
  // difference is what enables "Update task profile" — see the note in LeversPanel about why
  // this is a button rather than a live recompute.
  const [selectedAgents, setSelectedAgents] = useState<Set<string>>(new Set());
  const [appliedAgents, setAppliedAgents] = useState<Set<string>>(new Set());
  const [topAugs, setTopAugs] = useState(0);
  const [appliedTopAugs, setAppliedTopAugs] = useState(0);
  const [uplift, setUplift] = useState(0.5);
  const [appliedUplift, setAppliedUplift] = useState(0.5);

  const [title, setTitle] = useState("New job");
  const [headcount, setHeadcount] = useState(1);
  const [lines, setLines] = useState<DesignedTaskLine[]>([]);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<DesignedJob | null>(null);
  const [dragging, setDragging] = useState<PoolCluster | null>(null);

  const hpw = status?.hours_per_fte_week ?? 37.5;
  const unit = pool?.unit ?? (status?.has_headcount ? "FTE" : "role-weeks");

  // Which augmentation ids "top X per role" resolves to. One helper rather than a copy per
  // call site: the rule has to mean the same thing in the request and in the panel's summary,
  // and two implementations of it would eventually stop agreeing.
  const appliedSkillIds = useMemo(
    () => topSkillsPerRole(levers, appliedTopAugs),
    [levers, appliedTopAugs],
  );

  const leversDirty =
    selectedAgents.size !== appliedAgents.size ||
    [...selectedAgents].some((a) => !appliedAgents.has(a)) ||
    topAugs !== appliedTopAugs ||
    uplift !== appliedUplift;

  const refreshPool = useCallback(
    async (opts?: { agents?: Set<string>; skills?: string[]; up?: number; editing?: string | null }) => {
      const agents = opts?.agents ?? appliedAgents;
      const skills = opts?.skills ?? appliedSkillIds;
      const up = opts?.up ?? appliedUplift;
      const editing = opts?.editing !== undefined ? opts.editing : editingId;
      const res = await api.apply({
        ...facets,
        agent_ids: [...agents],
        skill_ids: skills,
        uplift: up,
        editing_job_id: editing,
      });
      setPool(res);
    },
    [api, facets, appliedAgents, appliedSkillIds, appliedUplift, editingId],
  );

  const load = useCallback(async () => {
    try {
      const [w, st] = await Promise.all([wfApi.status(), api.status()]);
      setWfStatus(w);
      setStatus(st);
      setUplift(st.augmentation_uplift);
      setAppliedUplift(st.augmentation_uplift);
      if (!st.ready) return;
      const [o, lv, j, tg] = await Promise.all([
        api.facets(),
        api.levers(),
        api.jobs(),
        api.target(),
      ]);
      setOptions(o);
      setLevers(lv);
      setJobs(j.jobs);
      setTarget(tg);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [api, wfApi]);

  useEffect(() => {
    void load();
  }, [load]);

  // The pool follows the facets. When levers are applied and the filter moves, re-apply
  // automatically: the selection is still valid, only the base changed, and leaving the old
  // post-lever profile on screen against a new sample would be silently stale.
  useEffect(() => {
    if (!status?.ready) return;
    void refreshPool().catch((e) => setError(e instanceof Error ? e.message : String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status?.ready, facets, appliedAgents, appliedTopAugs, appliedUplift, editingId]);

  const gates = useMemo(() => (wfStatus ? studioGates(wfStatus) : {}), [wfStatus]);

  const capacity = useMemo(() => {
    const assigned = lines.reduce((s, l) => s + l.hours_per_week, 0);
    const cap = headcount * hpw;
    const over = Math.max(0, assigned - cap);
    return {
      headcount,
      hours_per_fte_week: hpw,
      capacity_hours_per_week: cap,
      assigned_hours_per_week: assigned,
      fill_pct: cap ? (100 * assigned) / cap : null,
      over_capacity: assigned > cap + 0.01,
      over_by_hours_per_week: over,
      over_by_fte: hpw ? over / hpw : null,
      spare_hours_per_week: Math.max(0, cap - assigned),
      required_headcount: hpw ? assigned / hpw : null,
      message: "",
    };
  }, [lines, headcount, hpw]);

  function addFromPool(cluster: PoolCluster, hours: number) {
    setLines((prev) => {
      const existing = prev.find(
        (l) => l.task_cluster_id === cluster.cluster_id && l.origin === "as_is",
      );
      // Dropping a cluster already in the design merges rather than duplicating it.
      if (existing) {
        return prev.map((l) =>
          l.id === existing.id
            ? { ...l, hours_per_week: Math.round((l.hours_per_week + hours) * 100) / 100 }
            : l,
        );
      }
      return [
        ...prev,
        {
          id: newLineId(),
          task_cluster_id: cluster.cluster_id,
          cluster_name: cluster.name,
          name: cluster.name,
          description: "",
          origin: "as_is" as const,
          hours_per_week: Math.round(hours * 100) / 100,
          agent_id: null,
          source_profile_key: null,
          contributing_tasks: [],
          lever_ids: [...appliedAgents, ...appliedSkillIds],
          automation_pct: cluster.automation,
          augmentation_pct: cluster.augmentation,
        },
      ];
    });
    setDirty(true);
  }

  /** Oversight lines the levers created, offered as work the new job must absorb. */
  function addOversight() {
    if (!pool?.added?.length) return;
    setLines((prev) => [
      ...prev,
      ...pool.added!
        .filter((a) => !prev.some((l) => l.name === a.name))
        .map((a) => ({
          id: newLineId(),
          task_cluster_id: a.task_cluster_id,
          cluster_name: a.cluster_name,
          name: a.name,
          description: a.description,
          origin: "agent_oversight" as const,
          hours_per_week: a.hours_per_week,
          agent_id: a.agent_id,
          source_profile_key: null,
          contributing_tasks: [],
          lever_ids: [a.agent_id],
          automation_pct: null,
          augmentation_pct: null,
        })),
    ]);
    setDirty(true);
  }

  function payload(): JobPayload {
    return {
      title,
      headcount,
      facets,
      selected_agent_ids: [...appliedAgents],
      selected_skill_ids: appliedSkillIds,
      tasks: lines.map((l) => ({
        id: l.id.startsWith("tmp-") ? null : l.id,
        task_cluster_id: l.task_cluster_id,
        cluster_name: l.cluster_name,
        name: l.name,
        description: l.description,
        origin: l.origin,
        hours_per_week: l.hours_per_week,
        agent_id: l.agent_id,
        source_profile_key: l.source_profile_key,
        contributing_tasks: l.contributing_tasks,
        lever_ids: l.lever_ids,
        automation_pct: l.automation_pct,
        augmentation_pct: l.augmentation_pct,
      })),
    };
  }

  async function save() {
    setBusy(true);
    try {
      if (editingId) await api.updateJob(editingId, payload());
      else await api.createJob(payload());
      const [j, tg] = await Promise.all([api.jobs(), api.target()]);
      setJobs(j.jobs);
      setTarget(tg);
      clearDraft();
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  function clearDraft() {
    setTitle("New job");
    setHeadcount(1);
    setLines([]);
    setEditingId(null);
    setDirty(false);
  }

  function editJob(job: DesignedJob) {
    setTitle(job.title);
    setHeadcount(job.headcount);
    setLines(job.tasks);
    setEditingId(job.id);
    setDirty(false);
    document.getElementById("wd-design")?.scrollIntoView({ block: "start" });
  }

  async function doDelete(job: DesignedJob) {
    setBusy(true);
    try {
      const res = await api.deleteJob(job.id);
      const [j, tg] = await Promise.all([api.jobs(), api.target()]);
      setJobs(j.jobs);
      setTarget(tg);
      if (editingId === job.id) clearDraft();
      setConfirmDelete(null);
      setError(null);
      setNotice(
        `Deleted "${res.title}" — ${res.hours_returned_to_pool.toFixed(1)} hours a week returned to the pool.`,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const [notice, setNotice] = useState<string | null>(null);

  // Below the workbench breakpoint the studio is a single column: drag is off (a touch drag there
  // competes with the page scroll, and the cells are too small to aim at) and both panels pin
  // their list view. Handing DndContext an empty sensor array is what switches drag off — nothing
  // can begin a drag, so no child needs to know it is disabled.
  const narrow = useIsNarrow();

  const sensors = useSensors(
    // A distance constraint rather than none, so a click on a cell's + button is still a click.
    useSensor(MouseSensor, { activationConstraint: { distance: 4 } }),
    // A delay on touch, or a drag steals the page's scroll gesture.
    useSensor(TouchSensor, { activationConstraint: { delay: 200, tolerance: 6 } }),
    useSensor(KeyboardSensor),
  );

  function onDragStart(e: DragStartEvent) {
    const c = e.active.data.current?.cluster as PoolCluster | undefined;
    if (c) setDragging(c);
  }

  function onDragEnd(e: DragEndEvent) {
    const c = e.active.data.current?.cluster as PoolCluster | undefined;
    setDragging(null);
    if (!c || e.over?.id !== "design") return;
    // The drop carries one holder's share — the number a designer wants nine times in ten, and
    // by construction it cannot overflow a one-person job. The full aggregate stays on the cell
    // and the hours are editable straight afterwards.
    addFromPool(c, c.hours_per_holder_week);
  }

  const gate = status && !status.ready;

  return (
    <DndContext
      sensors={narrow ? [] : sensors}
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      // The design panel relayouts as it fills, so a rect measured at drag start is stale by the
      // second drop.
      measuring={{ droppable: { strategy: MeasuringStrategy.Always } }}
    >
      <div className="mx-auto flex max-w-[1600px] gap-6 px-6 py-6">
        <aside className="sticky top-[76px] hidden h-fit w-56 shrink-0 lg:block">
          <div className="mb-3 space-y-1.5">
            <StudioToggle gates={gates} />
            <DemoReset />
          </div>
          <p className="mb-3 text-[11px] font-extrabold uppercase tracking-wider text-text-muted">
            Steps
          </p>
          <nav className="space-y-0.5">
            {ANCHORS.map((a, i) => (
              <a
                key={a.id}
                href={`#${a.id}`}
                className="flex w-full items-center gap-2 rounded-[7px] px-2.5 py-1.5 text-left text-[12px] text-text-secondary transition-colors hover:bg-panel"
              >
                <span className="text-text-muted">{i + 1}.</span>
                <span className="min-w-0 truncate">{a.title}</span>
              </a>
            ))}
          </nav>
        </aside>

        <main className="min-w-0 flex-1 space-y-4">
          <div className="lg:hidden">
            <StudioToggle gates={gates} />
          </div>

          {error && (
            <p className="rounded-[10px] border border-brand-border bg-brand-bg px-4 py-3 text-[12.5px] text-brand">
              {error}
            </p>
          )}
          {notice && (
            <p
              onClick={() => setNotice(null)}
              className="cursor-pointer rounded-[10px] border border-accent-border bg-accent-bg px-4 py-2.5 text-[12.5px] text-accent"
            >
              {notice}
            </p>
          )}

          {gate && (
            <div className="rounded-[var(--radius-modal)] border border-border bg-card px-6 py-5 shadow-modal">
              <p className="text-[15px] font-bold text-text">Work Design Studio is not ready</p>
              <p className="mt-1 text-[13px] leading-relaxed text-text-secondary">
                This studio reads the task taxonomy and applies AI levers to it. Still needed:{" "}
                {status!.missing.join(", ")}.
              </p>
              <ul className="mt-3 space-y-1">
                {status!.checks.map((c) => (
                  <li key={c.name} className="flex items-start gap-2 text-[12.5px]">
                    <span className={c.ok ? "text-success" : "text-text-muted"}>
                      {c.ok ? "✓" : "○"}
                    </span>
                    <span>
                      <span className={c.ok ? "text-text-secondary" : "text-text"}>{c.name}</span>
                      {c.detail && (
                        <span className="ml-1.5 text-text-muted">— {c.detail}</span>
                      )}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {status?.ready && options && levers && pool && (
            <>
              <FacetBar
                options={options}
                facets={facets}
                onChange={setFacets}
                pool={pool}
                unit={unit}
              />

              <div className="grid grid-cols-1 gap-4 items-start xl:grid-cols-[minmax(0,1.1fr)_minmax(0,1.2fr)_320px]">
                <div id="wd-pool" className="scroll-mt-24">
                  <PoolPanel pool={pool} unit={unit} onAdd={addFromPool} forceList={narrow} />
                </div>
                <div id="wd-design" className="scroll-mt-24">
                  <JobDesignPanel
                    title={title}
                    onTitle={(v) => {
                      setTitle(v);
                      setDirty(true);
                    }}
                    headcount={headcount}
                    onHeadcount={(v) => {
                      setHeadcount(v);
                      setDirty(true);
                    }}
                    lines={lines}
                    onLines={(next) => {
                      setLines(next);
                      setDirty(true);
                    }}
                    capacity={capacity}
                    unit={unit}
                    editingId={editingId}
                    dirty={dirty}
                    busy={busy}
                    onSave={save}
                    onClear={clearDraft}
                    onImport={() => setImportOpen(true)}
                    forceList={narrow}
                  />
                  {(pool.added?.length ?? 0) > 0 && (
                    <button
                      onClick={addOversight}
                      className="mt-2 w-full rounded-[8px] border border-dashed border-border px-3 py-2 text-[11.5px] font-semibold text-text-secondary transition-colors hover:border-accent hover:text-accent"
                    >
                      + Add the {pool.added!.length} oversight tasks the agents created
                    </button>
                  )}
                </div>
                <LeversPanel
                  levers={levers}
                  selectedAgents={selectedAgents}
                  onAgents={setSelectedAgents}
                  topAugmentations={topAugs}
                  onTopAugmentations={setTopAugs}
                  uplift={uplift}
                  onUplift={setUplift}
                  dirty={leversDirty}
                  busy={busy}
                  applied={pool}
                  onApply={() => {
                    setAppliedAgents(new Set(selectedAgents));
                    setAppliedTopAugs(topAugs);
                    setAppliedUplift(uplift);
                  }}
                />
              </div>

              <DesignedJobList
                jobs={jobs}
                target={target}
                unit={unit}
                xlsxUrl={api.xlsxUrl()}
                onEdit={editJob}
                onDelete={setConfirmDelete}
              />
            </>
          )}
        </main>
      </div>

      {/* A chip, not a copy of the cell: a cell copy is the wrong size in the destination
          anyway, and a chip stays readable for a 20px sliver. */}
      <DragOverlay dropAnimation={null} modifiers={[snapCenterToCursor]}>
        {dragging && (
          <div className="rounded-[8px] border border-accent bg-card px-2.5 py-1.5 shadow-[var(--shadow-elevated)]">
            <p className="text-[11.5px] font-bold text-text">{dragging.name}</p>
            <p className="text-[10.5px] tabular-nums text-text-muted">
              {dragging.hours_per_holder_week.toFixed(1)} h — one holder's share
            </p>
          </div>
        )}
      </DragOverlay>

      {importOpen && (
        <ImportModal
          api={api}
          headcount={headcount}
          onClose={() => setImportOpen(false)}
          onImport={(imported, key) => {
            setLines(imported);
            setTitle((t) => (t === "New job" ? "Redesigned role" : t));
            setDirty(true);
            setImportOpen(false);
            void key;
          }}
          pool={pool}
        />
      )}

      {confirmDelete && (
        <Modal
          title="Delete this job definition?"
          onClose={() => !busy && setConfirmDelete(null)}
        >
          <div className="space-y-3 text-[13px] text-text-secondary">
            <p>
              <strong className="text-text">{confirmDelete.title}</strong> holds{" "}
              {confirmDelete.capacity.assigned_hours_per_week.toFixed(1)} hours a week across{" "}
              {confirmDelete.tasks.length} lines.
            </p>
            <p className="rounded-[10px] border border-accent-border bg-accent-bg px-3 py-2 text-accent">
              Its work returns to the unreviewed pool, so nothing is lost — it becomes available
              to allocate again.
            </p>
            <div className="flex justify-end gap-2 pt-1">
              <Button onClick={() => setConfirmDelete(null)} disabled={busy}>
                Cancel
              </Button>
              <Button variant="primary" onClick={() => doDelete(confirmDelete)} disabled={busy}>
                {busy ? "Deleting…" : "Delete and return the work"}
              </Button>
            </div>
          </div>
        </Modal>
      )}
    </DndContext>
  );
}

/** Pick a role and take its task profile as a starting point. */
function ImportModal({
  api,
  headcount,
  pool,
  onClose,
  onImport,
}: {
  api: ReturnType<typeof workDesignApi>;
  headcount: number;
  pool: PoolResult | null;
  onClose: () => void;
  onImport: (lines: DesignedTaskLine[], profileKey: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Roles are read off the pool's own cluster rows, so the picker offers exactly the jobs in
  // the current filter rather than every job in the project.
  const roles = useMemo(() => {
    const m = new Map<string, string>();
    for (const c of pool?.clusters ?? []) {
      for (const r of c.roles) m.set(r.profile_key, r.title);
    }
    const q = query.trim().toLowerCase();
    return [...m.entries()]
      .filter(([, t]) => !q || t.toLowerCase().includes(q))
      .sort((a, b) => a[1].localeCompare(b[1]))
      .slice(0, 60);
  }, [pool, query]);

  async function pick(profileKey: string) {
    setBusy(true);
    try {
      const res = await api.importPreview(profileKey, headcount);
      onImport(
        res.lines.map((l, i) => ({
          id: `imp-${i}`,
          task_cluster_id: l.task_cluster_id,
          cluster_name: l.cluster_name,
          name: l.name,
          description: l.description ?? "",
          origin: "as_is",
          hours_per_week: l.hours_per_week,
          agent_id: null,
          source_profile_key: l.source_profile_key ?? profileKey,
          contributing_tasks: l.contributing_tasks ?? [],
          lever_ids: [],
          automation_pct: l.automation_pct ?? null,
          augmentation_pct: l.augmentation_pct ?? null,
        })),
        profileKey,
      );
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title="Import a job's task profile"
      subtitle={`Scaled to ${headcount} — proportions sum to 100 per role, so an import fills the job exactly.`}
      onClose={onClose}
    >
      <div className="space-y-2">
        <input
          autoFocus
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search the roles in this filter…"
          className="w-full rounded-[8px] border border-border bg-card px-3 py-1.5 text-[12px] text-text outline-none focus:border-accent"
        />
        {err && <p className="text-[12px] text-brand">{err}</p>}
        <div className="max-h-72 overflow-y-auto">
          {roles.length === 0 && (
            <p className="py-6 text-center text-[12px] text-text-muted">No role matches.</p>
          )}
          {roles.map(([key, t]) => (
            <button
              key={key}
              disabled={busy}
              onClick={() => pick(key)}
              className="flex w-full items-center justify-between gap-2 border-b border-border/60 px-1 py-1.5 text-left last:border-0 hover:bg-panel disabled:opacity-50"
            >
              <span className="min-w-0 truncate text-[12px] text-text">{t}</span>
              <span className="shrink-0 text-[10.5px] font-semibold text-accent">import</span>
            </button>
          ))}
        </div>
      </div>
    </Modal>
  );
}
